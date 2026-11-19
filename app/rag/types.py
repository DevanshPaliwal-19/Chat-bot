from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ParsedPage:
    text: str
    page_number: int | None = None
    meta: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    pages: list[ParsedPage]
    source_type: str
    title: str | None = None
    meta: dict[str, str] = field(default_factory=dict)  # only fields found in the source itself

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


@dataclass
class ChunkDraft:
    text: str
    page_number: int | None
    section: str | None
    meta: dict[str, str] = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    embed_key: str
    content_hash: str
    source_document: str
    source_type: str
    title: str | None = None
    section: str | None = None
    source_url: str | None = None
    page_number: int | None = None
    service_category: str | None = None
    approval_status: str | None = None
    trust_tier: str | None = "tier_1"
    last_updated: str | None = None
    suspicious: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Chunk":
        d_copy = dict(d)
        if "trust_tier" not in d_copy:
            d_copy["trust_tier"] = "tier_1" if (d_copy.get("approval_status") or "").lower() in {"approved", "published", "final", "verified"} else "tier_2"
        return cls(**d_copy)

    def embed_text(self) -> str:
        head = " | ".join(x for x in (self.title, self.section) if x)
        return f"{head}\n{self.text}" if head else self.text


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float  # confidence in [0, 1]
    dense: float
    coverage: float
    stale: bool = False


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    confidence: float
    status: str = "ok"  # ok | index_unavailable | empty_index | no_match
    timings_ms: dict[str, float] = field(default_factory=dict)
    candidates: int = 0

    @classmethod
    def empty(cls, status: str = "no_match") -> "RetrievalResult":
        return cls(chunks=[], confidence=0.0, status=status)
