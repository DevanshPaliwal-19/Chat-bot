"""Document parsers: PDF, Markdown, TXT, HTML, JSONL, DOCX, XLSX, JSON.
Metadata is preserved across parsing, chunking, and indexing.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from .types import ParsedDocument, ParsedPage

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".jsonl": "jsonl",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".json": "json",
}
ALLOWED_META_KEYS = {
    "title", "source_url", "approval_status", "last_updated",
    "service_category", "trust_tier", "document_id", "subcategory",
}
_FRONT_MATTER = re.compile(r"\A﻿?---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)


class ParseError(Exception):
    """The file could not be turned into usable text."""


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower()
            if k in ALLOWED_META_KEYS and v.strip():
                meta[k] = v.strip().strip("\"'")
    return meta, text[m.end():]


def _first_heading(text: str) -> str | None:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def _extract_markdown_table_metadata(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    lines = text.splitlines()
    in_table = False
    for line in lines[:35]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#") and not in_table:
            continue
        if "|" in s:
            in_table = True
            parts = [p.strip() for p in s.strip("|").split("|")]
            if len(parts) >= 2:
                key_raw = parts[0].lower()
                val_raw = parts[1].strip()
                if "field" in key_raw or "---" in key_raw or not val_raw:
                    continue
                if "document id" in key_raw:
                    meta["document_id"] = val_raw
                elif "category" in key_raw and "sub" not in key_raw:
                    meta["service_category"] = val_raw
                elif "subcategory" in key_raw:
                    meta["subcategory"] = val_raw
                elif "source" in key_raw:
                    meta["source_url"] = val_raw.split(";")[0].strip()
                elif "status" in key_raw:
                    meta["approval_status"] = val_raw
                elif "reviewed" in key_raw or "updated" in key_raw or "date" in key_raw:
                    meta["last_updated"] = val_raw
        elif in_table:
            break
    return meta


def parse_markdown_or_text(path: Path, source_type: str) -> ParsedDocument:
    meta, body = _split_front_matter(_read_text(path))
    if source_type == "markdown":
        tbl_meta = _extract_markdown_table_metadata(body)
        for k, v in tbl_meta.items():
            meta.setdefault(k, v)
    body = _normalize(body)
    if not body:
        raise ParseError("document is empty")
    title = meta.get("title") or (_first_heading(body) if source_type == "markdown" else None)

    is_kb_final = any(part == "Osiyatech_Chatbot_final" for part in path.parts)
    raw_status = meta.get("approval_status", "")
    if raw_status:
        norm = "approved" if any(w in raw_status.lower() for w in ("verified", "approved", "documented", "published", "final", "listed")) else raw_status
        meta["approval_status"] = norm
        meta["trust_tier"] = "tier_1" if norm == "approved" else "tier_2"
    elif is_kb_final:
        meta["approval_status"] = "approved"
        meta["trust_tier"] = "tier_1"

    return ParsedDocument([ParsedPage(body, meta=dict(meta))], source_type, title, meta)


def parse_html(path: Path) -> ParsedDocument:
    soup = BeautifulSoup(_read_text(path), "html.parser")
    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or "").lower()
        if name in ALLOWED_META_KEYS and tag.get("content"):
            meta[name] = tag["content"].strip()
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href") and "source_url" not in meta:
        meta["source_url"] = canonical["href"].strip()
    title = meta.get("title") or (soup.title.get_text(strip=True) if soup.title else None)
    for t in soup(["script", "style", "noscript", "nav", "footer", "form", "svg"]):
        t.decompose()
    lines: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "pre", "blockquote"]):
        if el.name in {"li", "td", "th", "blockquote"} and el.find(["p", "li"]):
            continue  # avoid duplicating nested block text
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text:
            continue
        if el.name.startswith("h") and len(el.name) == 2:
            lines.append(f"{'#' * int(el.name[1])} {text}")
        elif el.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    body = _normalize("\n\n".join(lines))
    if not body:
        raise ParseError("no readable text in HTML")
    return ParsedDocument([ParsedPage(body)], "html", title, meta)


def parse_pdf(path: Path) -> ParsedDocument:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ParseError("PDF is encrypted")
        pages: list[ParsedPage] = []
        doc_meta = {
            "approval_status": "approved",
            "trust_tier": "tier_1",
        }
        for i, page in enumerate(reader.pages, start=1):
            text = _normalize(page.extract_text() or "")
            if text:
                p_meta = dict(doc_meta)
                pages.append(ParsedPage(text, i, meta=p_meta))
        info = reader.metadata
        title = str(info.title).strip() if info and info.title else None
        if not title and pages:
            first_line = pages[0].text.splitlines()[0].strip()
            if len(first_line) <= 80:
                title = first_line
        title = title or path.stem.replace("_", " ").title()
        doc_meta["title"] = title
    except ParseError:
        raise
    except Exception as exc:  # pypdf raises many different errors for corrupt files
        raise ParseError(f"PDF could not be read ({type(exc).__name__})") from exc
    if not pages:
        raise ParseError("PDF has no extractable text (scanned images need OCR)")
    return ParsedDocument(pages, "pdf", title, doc_meta)


def parse_jsonl(path: Path) -> ParsedDocument:
    raw = _read_text(path)
    pages: list[ParsedPage] = []
    is_kb_final = any(part == "Osiyatech_Chatbot_final" for part in path.parts)
    title = path.stem.replace("_", " ").title()
    doc_meta: dict[str, str] = {
        "title": title,
        "approval_status": "approved",
        "trust_tier": "tier_1",
    }
    for line_num, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        rec_title = record.get("title") or f"Unit {line_num}"
        rec_cat = record.get("category") or ""
        rec_sub = record.get("subcategory") or ""
        rec_content = record.get("content") or ""
        rec_url = record.get("source_url") or ""
        rec_status = record.get("verification_status") or "approved"
        norm_status = "approved" if any(w in str(rec_status).lower() for w in ("verified", "approved", "listed", "documented", "website", "rule", "team")) or is_kb_final else str(rec_status)
        related = record.get("related_questions")
        related_str = f"\n\n**Related Questions:** {', '.join(related)}" if isinstance(related, list) and related else ""
        header = f"# {rec_title}"
        if rec_cat:
            header += f"\n**Category:** {rec_cat}" + (f" - {rec_sub}" if rec_sub else "")
        if rec_url:
            header += f"\n**Source:** {rec_url}"
        page_text = _normalize(f"{header}\n\n{rec_content}{related_str}")
        if page_text:
            p_meta = {
                "title": rec_title,
                "service_category": rec_cat,
                "subcategory": rec_sub,
                "source_url": rec_url,
                "approval_status": norm_status,
                "trust_tier": "tier_1" if norm_status == "approved" else "tier_2",
                "last_updated": record.get("last_reviewed") or "",
            }
            pages.append(ParsedPage(text=page_text, page_number=line_num, meta=p_meta))
    if not pages:
        raise ParseError("JSONL document contains no valid records")
    return ParsedDocument(pages, "jsonl", title, doc_meta)


def parse_docx(path: Path) -> ParsedDocument:
    try:
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                raise ParseError("DOCX file missing word/document.xml")
            tree = ET.fromstring(z.read("word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            body = tree.find(f"{ns}body")
            if body is None:
                raise ParseError("DOCX document has empty body")
            lines: list[str] = []
            for child in body:
                tag = child.tag[len(ns):] if child.tag.startswith(ns) else child.tag
                if tag == "p":
                    text = "".join(node.text for node in child.iter(f"{ns}t") if node.text).strip()
                    if text:
                        p_style = child.find(f"{ns}pPr/{ns}pStyle")
                        val = p_style.get(f"{ns}val", "") if p_style is not None else ""
                        if "Heading" in val or "heading" in val or "Title" in val or "title" in val:
                            lines.append(f"## {text}")
                        else:
                            lines.append(text)
                elif tag == "tbl":
                    for tr in child.iter(f"{ns}tr"):
                        row = []
                        for tc in tr.iter(f"{ns}tc"):
                            tc_text = " ".join("".join(node.text for node in p.iter(f"{ns}t") if node.text).strip() for p in tc.iter(f"{ns}p"))
                            row.append(tc_text.strip())
                        if any(row):
                            lines.append("| " + " | ".join(row) + " |")
        body_text = _normalize("\n\n".join(lines))
        if not body_text:
            raise ParseError("DOCX file contains no readable text")
        title = _first_heading(body_text) or path.stem.replace("_", " ").title()
        meta = {
            "title": title,
            "approval_status": "approved",
            "trust_tier": "tier_1",
            "service_category": "Company Information",
        }
        return ParsedDocument([ParsedPage(body_text, 1, meta=meta)], "docx", title, meta)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"DOCX could not be read ({type(exc).__name__})") from exc


def parse_xlsx(path: Path) -> ParsedDocument:
    try:
        pages: list[ParsedPage] = []
        is_kb_final = any(part == "Osiyatech_Chatbot_final" for part in path.parts)
        doc_title = path.stem.replace("_", " ").title()
        doc_meta: dict[str, str] = {
            "title": doc_title,
            "approval_status": "approved",
            "trust_tier": "tier_1",
        }
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                s_tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
                ns_s = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                for si in s_tree.iter(f"{ns_s}si"):
                    shared_strings.append("".join(node.text for node in si.iter(f"{ns_s}t") if node.text))

            sheet_names = sorted([n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")])
            ns_w = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            page_counter = 1

            for sheet_name in sheet_names:
                tree = ET.fromstring(z.read(sheet_name))
                rows: list[list[str]] = []
                for row in tree.iter(f"{ns_w}row"):
                    row_vals: list[str] = []
                    for c in row.iter(f"{ns_w}c"):
                        cell_type = c.get("t")
                        is_node = c.find(f"{ns_w}is")
                        v_node = c.find(f"{ns_w}v")
                        val = ""
                        if is_node is not None:
                            val = "".join(node.text for node in is_node.iter(f"{ns_w}t") if node.text)
                        elif cell_type == "s" and v_node is not None and v_node.text:
                            idx = int(v_node.text)
                            val = shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
                        elif v_node is not None and v_node.text:
                            val = v_node.text
                        row_vals.append(val.strip())
                    if any(row_vals):
                        rows.append(row_vals)

                if not rows:
                    continue

                headers = [h.lower() for h in rows[0]]
                is_faq = "question" in headers and ("approved_answer" in headers or "answer" in headers)
                is_portfolio = "project_name" in headers and ("solution" in headers or "business_problem" in headers)

                if is_faq:
                    q_idx = headers.index("question")
                    a_idx = headers.index("approved_answer") if "approved_answer" in headers else headers.index("answer")
                    alt_idx = headers.index("alternative_questions") if "alternative_questions" in headers else -1
                    cat_idx = headers.index("category") if "category" in headers else -1
                    url_idx = headers.index("source_url") if "source_url" in headers else -1
                    status_idx = headers.index("verification_status") if "verification_status" in headers else -1

                    for r in rows[1:]:
                        question = r[q_idx] if q_idx < len(r) else ""
                        answer = r[a_idx] if a_idx < len(r) else ""
                        if not question or not answer:
                            continue
                        cat = r[cat_idx] if cat_idx >= 0 and cat_idx < len(r) else "Company FAQs"
                        alt = r[alt_idx] if alt_idx >= 0 and alt_idx < len(r) else ""
                        url = r[url_idx] if url_idx >= 0 and url_idx < len(r) else ""
                        status_val = r[status_idx] if status_idx >= 0 and status_idx < len(r) else "approved"
                        norm_status = "approved" if any(w in status_val.lower() for w in ("verified", "approved", "listed", "website", "published", "final")) or is_kb_final else status_val

                        text = f"# FAQ: {question}\n\n**Answer:** {answer}"
                        if cat:
                            text += f"\n\n**Category:** {cat}"
                        if alt:
                            text += f"\n**Alternative Questions:** {alt}"
                        if url:
                            text += f"\n**Source:** {url}"

                        p_meta = {
                            "title": question,
                            "service_category": cat,
                            "source_url": url,
                            "approval_status": norm_status,
                            "trust_tier": "tier_1" if norm_status == "approved" else "tier_2",
                        }
                        pages.append(ParsedPage(_normalize(text), page_counter, meta=p_meta))
                        page_counter += 1

                elif is_portfolio:
                    name_idx = headers.index("project_name")
                    prob_idx = headers.index("business_problem") if "business_problem" in headers else -1
                    sol_idx = headers.index("solution") if "solution" in headers else -1
                    tech_idx = headers.index("technologies") if "technologies" in headers else -1
                    ind_idx = headers.index("industry") if "industry" in headers else -1
                    res_idx = headers.index("verified_results") if "verified_results" in headers else -1
                    url_idx = headers.index("source_url") if "source_url" in headers else -1
                    status_idx = headers.index("verification_status") if "verification_status" in headers else -1

                    for r in rows[1:]:
                        pname = r[name_idx] if name_idx < len(r) else ""
                        if not pname:
                            continue
                        ind = r[ind_idx] if ind_idx >= 0 and ind_idx < len(r) else "Technology"
                        sol = r[sol_idx] if sol_idx >= 0 and sol_idx < len(r) else ""
                        prob = r[prob_idx] if prob_idx >= 0 and prob_idx < len(r) else ""
                        tech = r[tech_idx] if tech_idx >= 0 and tech_idx < len(r) else ""
                        res = r[res_idx] if res_idx >= 0 and res_idx < len(r) else ""
                        url = r[url_idx] if url_idx >= 0 and url_idx < len(r) else ""
                        status_val = r[status_idx] if status_idx >= 0 and status_idx < len(r) else "approved"
                        norm_status = "approved" if any(w in status_val.lower() for w in ("verified", "approved", "listed", "website", "published", "final")) or is_kb_final else status_val

                        parts = [f"# Project: {pname}"]
                        if ind:
                            parts.append(f"**Industry:** {ind}")
                        if prob:
                            parts.append(f"**Business Problem:** {prob}")
                        if sol:
                            parts.append(f"**Solution:** {sol}")
                        if tech:
                            parts.append(f"**Technologies:** {tech}")
                        if res:
                            parts.append(f"**Verified Results:** {res}")
                        if url:
                            parts.append(f"**Source:** {url}")

                        p_meta = {
                            "title": f"Project: {pname}",
                            "service_category": ind,
                            "source_url": url,
                            "approval_status": norm_status,
                            "trust_tier": "tier_1" if norm_status == "approved" else "tier_2",
                        }
                        pages.append(ParsedPage(_normalize("\n\n".join(parts)), page_counter, meta=p_meta))
                        page_counter += 1

                else:
                    # General sheet table
                    table_lines: list[str] = []
                    for r in rows:
                        table_lines.append("| " + " | ".join(r) + " |")
                    sheet_text = _normalize("\n".join(table_lines))
                    if sheet_text:
                        p_meta = {
                            "title": doc_title,
                            "approval_status": "approved",
                            "trust_tier": "tier_1",
                        }
                        pages.append(ParsedPage(sheet_text, page_counter, meta=p_meta))
                        page_counter += 1

        if not pages:
            raise ParseError("XLSX document contains no readable data")
        return ParsedDocument(pages, "xlsx", doc_title, doc_meta)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"XLSX could not be read ({type(exc).__name__})") from exc


def parse_json(path: Path) -> ParsedDocument:
    try:
        raw = _read_text(path)
        data = json.loads(raw)
        title = path.stem.replace("_", " ").title()
        meta = {"title": title, "approval_status": "approved", "trust_tier": "tier_1"}
        pages: list[ParsedPage] = []
        if isinstance(data, list):
            for i, item in enumerate(data, start=1):
                item_text = _normalize(json.dumps(item, indent=2))
                if item_text:
                    pages.append(ParsedPage(item_text, i, meta=dict(meta)))
        elif isinstance(data, dict):
            pages.append(ParsedPage(_normalize(json.dumps(data, indent=2)), 1, meta=dict(meta)))
        if not pages:
            raise ParseError("JSON document is empty")
        return ParsedDocument(pages, "json", title, meta)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"JSON could not be read ({type(exc).__name__})") from exc


def parse_file(path: Path) -> ParsedDocument:
    kind = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
    if kind is None:
        raise ParseError(f"unsupported file type '{path.suffix}'")
    try:
        if kind == "pdf":
            return parse_pdf(path)
        if kind == "html":
            return parse_html(path)
        if kind == "jsonl":
            return parse_jsonl(path)
        if kind == "docx":
            return parse_docx(path)
        if kind == "xlsx":
            return parse_xlsx(path)
        if kind == "json":
            return parse_json(path)
        return parse_markdown_or_text(path, kind)
    except ParseError:
        raise
    except OSError as exc:
        raise ParseError(f"file could not be read ({type(exc).__name__})") from exc
