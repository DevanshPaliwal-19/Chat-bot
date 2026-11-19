"""Language utilities shared by embedding, BM25 and chunk de-duplication (no topic knowledge here)."""
from __future__ import annotations

import hashlib
import re

STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from had has have how i if in into is it its
    me my of on or our so such than that the their them then there these they this to us was we were what when
    where which who why will with would you your about also any more most other some tell please want need like
    get give know let make just really very""".split()
)

_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9]|[a-z0-9]")


def stem(token: str) -> str:
    """Light, consistent suffix stripping: service/services, create/created/creating, company/companies
    all map to one form. Not linguistically perfect; it only needs to be consistent for matching."""
    t = token
    if len(t) <= 3:
        return t
    if t.endswith("ies") and len(t) > 4:
        t = t[:-3] + "y"
    elif t.endswith(("sses", "shes", "ches", "xes", "zes")):
        t = t[:-2]
    elif t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    for suffix in ("ingly", "edly", "ing", "ed"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            t = t[: -len(suffix)]
            if len(t) > 3 and t[-1] == t[-2] and t[-1] not in "lsz":
                t = t[:-1]
            break
    if t.endswith("e") and len(t) > 3:
        t = t[:-1]
    return t


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    tokens = _TOKEN.findall(text.lower())
    if not keep_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    return [stem(t.strip(".-")) for t in tokens if t.strip(".-")]


def normalize_for_hash(text: str) -> str:
    return " ".join(text.lower().split())


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


def sha1_short(text: str, n: int = 12) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]
