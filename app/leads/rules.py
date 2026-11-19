"""Pure lead helpers: validation, contact extraction, merging, qualification score."""
from __future__ import annotations

import re

LEAD_FIELDS = ("name", "company", "email", "phone", "project_type", "requirements", "timeline", "budget", "preferred_contact")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_PHONE_IN_TEXT = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{7,17}\d(?![\w])")

_SCORE_WEIGHTS = {
    "project_type": 20, "requirements": 20, "timeline": 15, "email": 20,
    "company": 10, "phone": 5, "budget": 5, "name": 5,
}


def is_valid_email(value: str | None) -> bool:
    return bool(value and len(value) <= 200 and _EMAIL.match(value.strip()))


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        return None
    return ("+" if value.strip().startswith("+") else "") + digits


def extract_contact_details(text: str) -> dict[str, str]:
    """Deterministic extraction of structured contact data (not question routing)."""
    found: dict[str, str] = {}
    if m := _EMAIL_IN_TEXT.search(text):
        found["email"] = m.group(0)
    for m in _PHONE_IN_TEXT.finditer(text):
        phone = normalize_phone(m.group(0))
        if phone:
            found["phone"] = phone
            break
    return found


def clean_field(key: str, value: object, limit: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    v = " ".join(value.split())[:limit].strip()
    if not v or v.lower() in {"null", "none", "n/a", "unknown", "not provided", "not specified"}:
        return None
    if key == "email":
        return v if is_valid_email(v) else None
    if key == "phone":
        return normalize_phone(v)
    return v


def merge_fields(existing: dict[str, str], new: dict[str, object]) -> dict[str, str]:
    merged = dict(existing)
    for key in LEAD_FIELDS:
        cleaned = clean_field(key, new.get(key))
        if cleaned:
            merged[key] = cleaned
    return merged


def qualification_score(fields: dict[str, str]) -> int:
    score = 0
    for key, weight in _SCORE_WEIGHTS.items():
        if fields.get(key):
            score += weight
    return min(score, 100)


def lead_status_for(fields: dict[str, str], score: int, offer_score: int) -> str:
    if score >= offer_score:
        return "qualified"
    if fields.get("project_type") or fields.get("requirements"):
        return "exploring"
    return "none"
