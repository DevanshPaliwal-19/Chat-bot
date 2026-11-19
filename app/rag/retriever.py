"""Hybrid retrieval: dense (FAISS) + lexical (BM25) fused with RRF, then confidence-scored."""
from __future__ import annotations

import logging
import time
from datetime import date

from ..config import Settings
from .embeddings import Embedder
from .index import KnowledgeIndex
from .text import tokenize
from .types import RetrievalResult, RetrievedChunk

logger = logging.getLogger(__name__)
_RRF_K = 60

_ACRONYMS = {
    "ai": ["artificial", "intelligence"],
    "ml": ["machine", "learning"],
    "qa": ["quality", "assurance", "testing"],
    "erp": ["enterprise", "resource", "planning"],
    "crm": ["customer", "relationship", "management"],
    "bi": ["business", "intelligence"],
    "ui": ["user", "interface", "design"],
    "ux": ["user", "experience", "design"],
    "devops": ["cloud", "infrastructure", "ci/cd"],
}


def expand_tokens(tokens: list[str]) -> list[str]:
    """Expand common engineering and service acronyms for broader lexical recall."""
    expanded = list(tokens)
    for tok in tokens:
        syns = _ACRONYMS.get(tok.lower())
        if syns:
            expanded.extend(syns)
    seen: set[str] = set()
    out: list[str] = []
    for t in expanded:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class Retriever:
    def __init__(self, index: KnowledgeIndex | None, embedder: Embedder, settings: Settings) -> None:
        self.index = index
        self.embedder = embedder
        self.settings = settings

    @property
    def ready(self) -> bool:
        return self.index is not None and len(self.index.chunks) > 0

    def set_index(self, index: KnowledgeIndex | None) -> None:
        self.index = index

    def _allowed(self, i: int, service_category: str | None) -> bool:
        c = self.index.chunks[i]  # type: ignore[union-attr]
        if c.suspicious:
            return False
        # Tier 3 and internal-only documents are strictly forbidden from public retrieval
        if getattr(c, "trust_tier", "") == "tier_3" or "internal" in (c.approval_status or "").lower():
            return False
        doc_lower = (c.source_document or "").lower()
        if any(bad in doc_lower for bad in ("missing", "review_report", "_archive")):
            return False

        status = (c.approval_status or "").lower()
        if self.settings.require_approved_only:
            if status not in {"approved", "published", "final", "verified"}:
                return False
        elif status and status not in {"approved", "published", "final", "verified"}:
            return False  # explicitly draft / rejected / pending content is never served
        if service_category and (c.service_category or "").lower() != service_category.lower():
            return False
        return True

    def _is_stale(self, last_updated: str | None) -> bool:
        days = self.settings.stale_after_days
        if not days or not last_updated:
            return False
        try:
            return (date.today() - date.fromisoformat(last_updated[:10])).days > days
        except ValueError:
            return False

    def retrieve(self, query: str, *, top_k: int | None = None, service_category: str | None = None) -> RetrievalResult:
        if self.index is None:
            return RetrievalResult.empty("index_unavailable")
        if not self.index.chunks:
            return RetrievalResult.empty("empty_index")
        s = self.settings
        top_k = top_k or s.retrieval_top_k
        t0 = time.perf_counter()
        q_tokens = tokenize(query)
        if not q_tokens:
            return RetrievalResult.empty("no_match")

        expanded_tokens = expand_tokens(q_tokens)
        qvec = self.embedder.embed([query])[0]
        t_embed = time.perf_counter()
        dense = self.index.dense_search(qvec, s.retrieval_candidates)
        lexical = self.index.bm25_search(expanded_tokens, s.retrieval_candidates)
        t_search = time.perf_counter()

        dense_score = {i: sc for i, sc in dense}
        fused: dict[int, float] = {}
        for rank, (i, _) in enumerate(dense):
            fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, (i, _) in enumerate(lexical):
            fused[i] = fused.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)

        ranked = [i for i, _ in sorted(fused.items(), key=lambda x: x[1], reverse=True) if self._allowed(i, service_category)]

        results: list[RetrievedChunk] = []
        kept_term_sets: list[set[str]] = []
        seen_hashes: set[str] = set()
        budget = s.context_char_budget
        q_token_set = set(q_tokens)
        q_lower = query.strip().lower()

        for i in ranked:
            c = self.index.chunks[i]
            if c.content_hash in seen_hashes:
                continue
            terms = self.index._terms[i]
            if any(_jaccard(terms, k) > 0.85 for k in kept_term_sets):
                continue
            cos = dense_score.get(i)
            if cos is None:  # lexical-only hit: compute its dense similarity exactly
                cos = float(self.index.vectors[i] @ qvec)
            cov = self.index.coverage(q_tokens, i)

            # Relevance & evidence boosts
            bonus = 0.0
            chunk_title_tokens = set(tokenize((c.title or "") + " " + (c.section or "")))
            if q_token_set & chunk_title_tokens:
                bonus += 0.08
            if len(q_lower) >= 4 and q_lower in c.text.lower():
                bonus += 0.06
            if service_category and (c.service_category or "").lower() == service_category.lower():
                bonus += 0.05
            status_lower = (c.approval_status or "").lower()
            if status_lower in {"approved", "published", "final"} or "verified" in status_lower:
                bonus += 0.03

            raw_score = s.dense_weight * max(0.0, min(1.0, cos)) + (1 - s.dense_weight) * cov
            score = min(1.0, max(0.0, raw_score + bonus))
            if score < s.min_confidence * 0.5:
                continue
            if results and budget - len(c.text) < 0:
                continue
            budget -= len(c.text)
            seen_hashes.add(c.content_hash)
            kept_term_sets.append(terms)
            results.append(RetrievedChunk(c, round(score, 4), round(cos, 4), round(cov, 4), self._is_stale(c.last_updated)))
            if len(results) >= top_k:
                break

        results.sort(key=lambda r: r.score, reverse=True)
        confidence = results[0].score if results else 0.0
        t_end = time.perf_counter()
        return RetrievalResult(
            chunks=results,
            confidence=confidence,
            status="ok" if results else "no_match",
            timings_ms={
                "embed": round((t_embed - t0) * 1000, 1),
                "search": round((t_search - t_embed) * 1000, 1),
                "rank": round((t_end - t_search) * 1000, 1),
            },
            candidates=len(fused),
        )
