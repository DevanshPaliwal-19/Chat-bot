"""Post-generation checks: unsupported figures, false notification claims, prompt leakage, bad citations."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SENT = re.compile(r"(?<=[.!?])\s+")
_NUM_CORE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_FIGURE = re.compile(
    r"(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?:\b(?:rs\.?|inr|usd|eur|gbp)\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?:\b\d[\d,]*(?:\.\d+)?\s?(?:%|percent|per cent)(?!\w))"
    r"|(?:\b\d{1,3}(?:,\d{3})+\b)"
    r"|(?:\b\d+\+?\s+(?:years?|clients?|projects?|customers?|employees?|engineers?|developers?|countries|offices|members)\b)",
    re.I,
)
_NOTIFY_CLAIMS = re.compile(
    r"\b(?:i(?:'ve| have)|we(?:'ve| have))\s+(?:already\s+)?(?:notified|informed|alerted|contacted|emailed|forwarded|passed|sent|booked|scheduled)\b"
    r"|\b(?:team|someone|representative|colleague)\b[^.!?]{0,30}\b(?:has been|have been|will be)\s+(?:notified|informed|alerted)\b"
    r"|\b(?:someone|a representative|our team|the team|a member of (?:our|the) team)\s+will\s+(?:contact|call|email|reach out to|get back to)\s+you\b",
    re.I,
)
_CITATION = re.compile(r"\s?\[(\d{1,2})\]")
SAFE_FALLBACK = "I can't confirm that from the verified information I have. I can help you reach the team for the details."
LEAK_REPLY = "I can't share that, but I'm happy to help with questions about the company and its services."


@dataclass
class ValidationOutcome:
    text: str
    issues: list[str] = field(default_factory=list)
    is_truncated: bool = False

    @property
    def modified(self) -> bool:
        return bool(self.issues)


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUM_CORE.finditer(text)}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def longest_common_run(a: str, b: str) -> int:
    """Length (in words) of the longest run of consecutive words shared by both texts."""
    x, y = _words(a), _words(b)
    if not x or not y:
        return 0
    best, prev = 0, [0] * (len(y) + 1)
    for i in range(1, len(x) + 1):
        cur = [0] * (len(y) + 1)
        xi = x[i - 1]
        for j in range(1, len(y) + 1):
            if xi == y[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _filter_sentence(sent: str, allowed_numbers: set[str], issues: list[str]) -> bool:
    figures = [m.group(0) for m in _FIGURE.finditer(sent)]
    if any(not (_numbers(f) & allowed_numbers) for f in figures):
        issues.append("unsupported_figure")
        return False
    if _NOTIFY_CLAIMS.search(sent):
        issues.append("false_notification_claim")
        return False
    return True


def _clean_paragraph_or_list_block(block: str, allowed_numbers: set[str], issues: list[str]) -> str:
    lines = block.split("\n")
    is_list_or_table = any(re.match(r"^\s*(?:\d+[\.\)]|[-*•]|#+|\|)\s*", l) for l in lines)
    if is_list_or_table:
        out_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or (stripped.startswith("|") and stripped.endswith("|")):
                out_lines.append(line)
                continue
            sents = _SENT.split(line)
            kept = [s for s in sents if _filter_sentence(s, allowed_numbers, issues)]
            if kept:
                out_lines.append(" ".join(kept))
        return "\n".join(out_lines)
    else:
        sents = _SENT.split(block)
        kept = [s for s in sents if _filter_sentence(s, allowed_numbers, issues)]
        return " ".join(kept).strip()


def _clean_markdown_text(text: str, allowed_numbers: set[str], issues: list[str]) -> str:
    # Protect fenced code blocks
    parts = re.split(r"(```[\s\S]*?```)", text)
    cleaned_parts = []
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            cleaned_parts.append(part)
        else:
            paras = re.split(r"(\n{2,})", part)
            cleaned_paras = []
            for p in paras:
                if re.match(r"^\n{2,}$", p):
                    cleaned_paras.append(p)
                elif p:
                    cleaned_paras.append(_clean_paragraph_or_list_block(p, allowed_numbers, issues))
            cleaned_parts.append("".join(cleaned_paras))
    return "".join(cleaned_parts).strip()


def validate_answer(
    answer: str,
    *,
    supporting_text: str,
    system_prompt: str,
    valid_citations: int,
    strip_citations: bool,
    max_chars: int = 4000,
    leak_run_words: int = 20,
) -> ValidationOutcome:
    issues: list[str] = []
    text = answer.strip()
    text = re.sub(r"^(assistant|osiya)\s*:\s*", "", text, flags=re.I).strip()

    # A leak means reproducing a long stretch of the instructions. Short overlaps are normal and expected: the prompt
    # tells the model what to say when it has no information, so a good answer may repeat that wording.
    if longest_common_run(text, system_prompt) >= leak_run_words:
        return ValidationOutcome(LEAK_REPLY, ["prompt_leak"])

    allowed_numbers = _numbers(supporting_text)
    text = _clean_markdown_text(text, allowed_numbers, issues)

    def _fix(m: re.Match[str]) -> str:
        return "" if strip_citations or not 1 <= int(m.group(1)) <= valid_citations else m.group(0)

    cleaned = _CITATION.sub(_fix, text)
    if cleaned != text and not strip_citations:
        issues.append("invalid_citation")
    text = cleaned.strip()

    is_truncated = False
    if len(text) > max_chars:
        cut = text[:max_chars]
        last = max(
            cut.rfind(".\n"),
            cut.rfind(". "),
            cut.rfind("!\n"),
            cut.rfind("! "),
            cut.rfind("?\n"),
            cut.rfind("? "),
            cut.rfind("\n\n"),
        )
        text = cut[: last + 1].rstrip() if last > max_chars // 2 else cut.rstrip() + "…"
        issues.append("truncated")
        is_truncated = True

    if not text:
        return ValidationOutcome(SAFE_FALLBACK, issues + ["emptied"], is_truncated=False)
    return ValidationOutcome(text, issues, is_truncated=is_truncated)
