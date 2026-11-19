"""Retrieval evaluation. Used by tests/test_rag_eval.py and scripts/eval_rag.py (point it at your own dataset)."""
from __future__ import annotations

from typing import Any

from .retriever import Retriever


def evaluate(retriever: Retriever, dataset: list[dict[str, Any]], *, min_confidence: float, k: int = 4) -> dict[str, Any]:
    """Dataset items: {id, question, type: supported|unsupported, expected_docs: [...], expected_facts: [...]}.

    Metrics
      recall_at_k        fraction of supported questions whose expected document appears in the top k
      mrr                mean reciprocal rank of the first expected document
      fact_coverage      fraction of expected facts found (case-insensitive) in the retrieved text
      grounded_rate      fraction of supported questions that clear the confidence threshold
      unsupported_reject fraction of unsupported questions that stay below the threshold
    """
    sup = [d for d in dataset if d.get("type", "supported") == "supported"]
    uns = [d for d in dataset if d.get("type") == "unsupported"]
    hits, rr, facts_found, facts_total, grounded, failures = 0, 0.0, 0, 0, 0, []
    for item in sup:
        res = retriever.retrieve(item["question"], top_k=k)
        docs = [r.chunk.source_document for r in res.chunks]
        expected = set(item.get("expected_docs", []))
        rank = next((i for i, d in enumerate(docs, 1) if d in expected), None)
        if rank:
            hits += 1
            rr += 1 / rank
        else:
            failures.append({"id": item["id"], "reason": "expected document not retrieved", "got": docs})
        text = " ".join(r.chunk.text for r in res.chunks).lower()
        for fact in item.get("expected_facts", []):
            facts_total += 1
            facts_found += fact.lower() in text
        grounded += res.confidence >= min_confidence
    rejected = 0
    for item in uns:
        res = retriever.retrieve(item["question"], top_k=k)
        if res.confidence < min_confidence:
            rejected += 1
        else:
            failures.append({"id": item["id"], "reason": "unsupported question cleared the threshold", "confidence": res.confidence})
    return {
        "supported": len(sup), "unsupported": len(uns), "k": k, "min_confidence": min_confidence,
        "recall_at_k": hits / len(sup) if sup else None,
        "mrr": rr / len(sup) if sup else None,
        "fact_coverage": facts_found / facts_total if facts_total else None,
        "grounded_rate": grounded / len(sup) if sup else None,
        "unsupported_reject": rejected / len(uns) if uns else None,
        "failures": failures,
    }
