"""Incremental ingestion: scan -> parse -> dedupe -> chunk -> embed (cached) -> index -> report."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import numpy as np

from ..config import Settings
from ..security.sanitize import detect_injection
from .chunking import chunk_document
from .embeddings import Embedder
from .index import IndexUnavailable, KnowledgeIndex
from .parsers import SUPPORTED_EXTENSIONS, ParseError, parse_file
from .text import content_hash, sha1_short
from .types import Chunk

logger = logging.getLogger(__name__)


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    duplicates: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    suspicious_chunks: list[str] = field(default_factory=list)
    chunk_count: int = 0
    index_version: int = 0
    embedder: str = ""
    full_rebuild: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


def _file_hash(path: Path) -> str:
    h = sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()


_INTERNAL_PATTERNS = [
    "07_missing_and_unverified_information",
    "08_website_review_report",
    "missing_and_unverified",
    "website_review_report",
    "review_report",
    "_archive",
    "internal_audit",
]

_SERVICE_NAME_MAP = {
    "web_development": "Web Development",
    "web-development": "Web Development",
    "mobile_app_development": "Mobile App Development",
    "mobile-development": "Mobile App Development",
    "ai_ml_development": "AI & Machine Learning",
    "ai-ml-development": "AI & Machine Learning",
    "ai_agents_workflow_automation": "AI Agents & Workflow Automation",
    "ai-agents-automation": "AI Agents & Workflow Automation",
    "bi_analytics": "BI & Data Analytics",
    "bi-data-analytics": "BI & Data Analytics",
    "cloud_devops": "Cloud & DevOps",
    "cloud-devops": "Cloud & DevOps",
    "cybersecurity": "Cybersecurity",
    "custom_software_core_business_systems": "Custom Software & Core Business Systems",
    "custom-software-systems": "Custom Software & Core Business Systems",
    "enterprise_software_development": "Enterprise Software Development",
    "enterprise-software-development": "Enterprise Software Development",
    "enterprise_system_integration": "Enterprise System Integration",
    "enterprise-system-integration": "Enterprise System Integration",
    "erp_crm_solutions": "ERP & CRM Solutions",
    "erp-crm-solutions": "ERP & CRM Solutions",
    "database_cloud_migration": "Database & Cloud Migration",
    "database-cloud-migration": "Database & Cloud Migration",
    "dedicated_engineering_teams": "Dedicated Engineering Teams",
    "dedicated-engineering-teams": "Dedicated Engineering Teams",
    "qa_automated_testing": "QA & Automated Testing",
    "qa-automated-testing": "QA & Automated Testing",
    "ui_ux_design": "UI/UX Design & Prototyping",
    "ui-ux-design": "UI/UX Design & Prototyping",
    "company": "Company Information",
    "policies": "Company Policies",
    "05_policies": "Company Policies",
}


def is_internal_document(rel_path: str) -> bool:
    rel_lower = rel_path.lower()
    return any(pat in rel_lower for pat in _INTERNAL_PATTERNS)


def determine_service_category(rel_path: str, doc_category: str | None = None) -> str | None:
    if doc_category and doc_category.strip() and doc_category != "Osiyatech_Chatbot_final":
        return doc_category.strip()
    parts = Path(rel_path).parts
    for part in reversed(parts[:-1]):
        clean_part = re.sub(r"^\d+_", "", part).lower()
        if clean_part in _SERVICE_NAME_MAP:
            return _SERVICE_NAME_MAP[clean_part]
    if len(parts) > 1:
        clean_top = re.sub(r"^\d+_", "", parts[0]).lower()
        if clean_top in _SERVICE_NAME_MAP:
            return _SERVICE_NAME_MAP[clean_top]
        if parts[0] != "Osiyatech_Chatbot_final":
            return parts[0]
    stem_clean = re.sub(r"^\d+_", "", Path(rel_path).stem).lower()
    if "company" in stem_clean or "about" in stem_clean:
        return "Company Information"
    if "faq" in stem_clean:
        return "Company FAQs"
    if "portfolio" in stem_clean:
        return "Portfolio & Case Studies"
    if "policy" in stem_clean or "privacy" in stem_clean or "terms" in stem_clean:
        return "Company Policies"
    return parts[0] if len(parts) > 1 and parts[0] != "Osiyatech_Chatbot_final" else None


class Ingestor:
    def __init__(self, settings: Settings, embedder: Embedder) -> None:
        self.settings = settings
        self.embedder = embedder

    def _scan(self, report: IngestReport) -> dict[str, Path]:
        root = self.settings.kb_dir
        found: dict[str, Path] = {}
        if not root.exists():
            return found
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part.startswith((".", "_")) for part in rel.parts):
                continue  # hidden / underscore-prefixed files are deliberately excluded
            rel_str = rel.as_posix()
            if is_internal_document(rel_str):
                report.skipped.append({"path": rel_str, "reason": "internal_tier_3_excluded"})
                continue
            if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                report.skipped.append({"path": rel_str, "reason": f"unsupported file type '{p.suffix}'"})
                continue
            found[rel_str] = p
        return found

    def run(self, full_rebuild: bool = False) -> IngestReport:
        t0 = time.perf_counter()
        report = IngestReport(embedder=self.embedder.name, full_rebuild=full_rebuild)
        old: KnowledgeIndex | None = None
        if not full_rebuild:
            try:
                old = KnowledgeIndex.load(self.settings.index_dir)
                if old.embedder_name != self.embedder.name:
                    logger.warning("embedder changed (%s -> %s): full rebuild", old.embedder_name, self.embedder.name)
                    old, report.full_rebuild = None, True
            except IndexUnavailable:
                old = None
        old_docs: dict[str, dict] = (old.manifest.get("documents", {}) if old else {})
        old_chunks_by_doc: dict[str, list[Chunk]] = {}
        vec_cache: dict[str, np.ndarray] = {}
        if old is not None:
            for c, v in zip(old.chunks, old.vectors):
                old_chunks_by_doc.setdefault(c.source_document, []).append(c)
                vec_cache[c.embed_key] = v

        found = self._scan(report)
        report.removed = sorted(set(old_docs) - set(found))

        new_docs: dict[str, dict] = {}
        kept_chunks: list[Chunk] = []
        seen_content: dict[str, str] = {}  # document content hash -> rel path

        for rel, path in found.items():
            try:
                fhash = _file_hash(path)
                prev = old_docs.get(rel)
                if prev and prev.get("file_hash") == fhash and rel in old_chunks_by_doc:
                    owner = seen_content.get(prev["content_hash"])
                    if owner is None:
                        seen_content[prev["content_hash"]] = rel
                        new_docs[rel] = prev
                        kept_chunks.extend(old_chunks_by_doc[rel])
                        report.unchanged.append(rel)
                        continue
                doc = parse_file(path)
                dhash = content_hash(doc.full_text)
                if dhash in seen_content:
                    report.duplicates.append({"path": rel, "duplicate_of": seen_content[dhash]})
                    continue
                seen_content[dhash] = rel
                drafts = chunk_document(doc, self.settings.chunk_chars, self.settings.chunk_overlap_chars)
                if not drafts:
                    raise ParseError("no usable text after chunking")
                doc_id = sha1_short(rel)
                doc_category = determine_service_category(rel, doc.meta.get("service_category"))
                is_kb_final = any(part == "Osiyatech_Chatbot_final" for part in path.parts)
                doc_status = (doc.meta.get("approval_status") or "").lower()
                if doc_status:
                    norm_status = "approved" if any(w in doc_status for w in ("approved", "published", "final", "verified", "documented", "listed")) or is_kb_final else doc_status
                    doc_tier = "tier_1" if norm_status == "approved" else "tier_2"
                    doc_approved = norm_status
                elif is_kb_final:
                    doc_approved = "approved"
                    doc_tier = "tier_1"
                else:
                    doc_approved = None
                    doc_tier = "tier_2"
                chunks: list[Chunk] = []
                for n, d in enumerate(drafts):
                    chunk_meta = d.meta if hasattr(d, "meta") else {}
                    c_status = chunk_meta.get("approval_status") or doc_approved
                    c_cat = chunk_meta.get("service_category") or doc_category
                    c_title = chunk_meta.get("title") or doc.title
                    c_url = chunk_meta.get("source_url") or doc.meta.get("source_url")
                    c_tier = chunk_meta.get("trust_tier") or doc_tier

                    if is_kb_final and (not c_status or c_status != "draft"):
                        c_status = "approved"
                        c_tier = "tier_1"

                    chunk = Chunk(
                        chunk_id=f"{doc_id}:{n:04d}",
                        document_id=doc_id,
                        text=d.text,
                        embed_key="",
                        content_hash=content_hash(d.text),
                        source_document=rel,
                        source_type=doc.source_type,
                        title=c_title,
                        section=d.section,
                        source_url=c_url,
                        page_number=d.page_number,
                        service_category=c_cat,
                        approval_status=c_status,
                        trust_tier=c_tier,
                        last_updated=chunk_meta.get("last_updated") or doc.meta.get("last_updated") or _iso_mtime(path),
                        suspicious=detect_injection(d.text),
                    )
                    chunk.embed_key = sha1_short(f"{self.embedder.name}|{chunk.embed_text()}", 20)
                    if chunk.suspicious:
                        report.suspicious_chunks.append(chunk.chunk_id)
                    chunks.append(chunk)
                kept_chunks.extend(chunks)
                new_docs[rel] = {
                    "document_id": doc_id, "file_hash": fhash, "content_hash": dhash,
                    "chunk_count": len(chunks), "source_type": doc.source_type, "title": doc.title,
                }
                (report.updated if prev else report.added).append(rel)
            except ParseError as exc:
                report.errors.append({"path": rel, "error": str(exc)})
                if rel in old_docs and rel in old_chunks_by_doc:  # keep the last good version searchable
                    new_docs[rel] = old_docs[rel]
                    kept_chunks.extend(old_chunks_by_doc[rel])
                    report.errors[-1]["note"] = "previous indexed version kept"
                    seen_content.setdefault(old_docs[rel]["content_hash"], rel)

        # embed only what we have not embedded before
        missing = [c for c in kept_chunks if c.embed_key not in vec_cache]
        if missing:
            vecs = self.embedder.embed([c.embed_text() for c in missing])
            for c, v in zip(missing, vecs):
                vec_cache[c.embed_key] = v
        dim = getattr(self.embedder, "dim", 1)
        vectors = np.vstack([vec_cache[c.embed_key] for c in kept_chunks]) if kept_chunks else np.zeros((0, dim), "float32")

        version = int(old.manifest.get("index_version", 0)) if old else 0
        if report.changed or old is None or report.full_rebuild:
            version += 1
        index = KnowledgeIndex(kept_chunks, vectors, self.embedder.name)
        if report.changed or old is None or report.full_rebuild or not (self.settings.index_dir / "manifest.json").exists():
            index.save(self.settings.index_dir, {"index_version": version, "documents": new_docs})
        report.chunk_count = len(kept_chunks)
        report.index_version = version
        report.duration_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "ingestion finished: +%d ~%d -%d =%d docs, %d chunks, %d errors, v%d",
            len(report.added), len(report.updated), len(report.removed), len(report.unchanged),
            report.chunk_count, len(report.errors), version,
        )
        return report
