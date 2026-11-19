"""Input hygiene and prompt-injection signals.

Injection detection is a *signal* (logged, and used to harden the prompt / exclude ingested chunks).
It is never used to route a visitor's question to a canned answer.
"""
from __future__ import annotations

import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t]{3,}")
_BLANKS = re.compile(r"\n{4,}")

_INJECTION = [
    r"ignore\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules?|messages?)",
    r"disregard\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|system)?\s*(instructions?|rules?|guidelines?|prompts?)",
    r"forget\s+(all\s+|everything\s+|your\s+)(previous\s+|prior\s+)?(instructions?|rules?)",
    r"(reveal|show|print|repeat|output|leak|display|tell me)\s+(me\s+)?(your|the)\s+(system|hidden|initial|internal|original)\s+(prompt|instructions?|message)",
    r"(system|developer)\s+prompt",
    r"developer\s+mode",
    r"jailbreak",
    r"\bdan\s+mode\b",
    r"override\s+(your|the|all)\s+(rules|instructions|guidelines|safety)",
    r"(print|show|reveal|dump|give|tell|read)\s+.{0,30}(environment\s+variables?|\.env\b|api[\s_-]?keys?|secrets?|passwords?|credentials)",
    r"<\s*/?\s*(system|context|instructions?)\s*>",
    r"\[\s*/?\s*(system|inst)\s*\]",
    r"you\s+are\s+no\s+longer\s+(an?\s+)?(assistant|bound)",
    r"act\s+as\s+(an?\s+)?(unrestricted|unfiltered|jailbroken)",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION), re.I | re.S)


def clean_text(text: str, max_chars: int | None = None) -> str:
    """Normalise visitor-supplied text: strip control chars, collapse whitespace, optionally truncate."""
    text = unicodedata.normalize("NFC", text or "")
    text = _CONTROL.sub("", text)
    text = _SPACES.sub("  ", text)
    text = _BLANKS.sub("\n\n\n", text).strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def detect_injection(text: str) -> bool:
    return bool(text and _INJECTION_RE.search(text))
