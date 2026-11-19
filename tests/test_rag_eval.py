"""RAG evaluation against a labelled dataset (fixtures only - answers never live in runtime code)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.embeddings import HashingEmbedder
from app.rag.evaluation import evaluate
from app.rag.index import KnowledgeIndex
from app.rag.retriever import Retriever

DATASET = json.loads((Path(__file__).parent / "eval" / "rag_eval.json").read_text(encoding="utf-8"))


@pytest.fixture
def report(ingested):
    r = Retriever(KnowledgeIndex.load(ingested.index_dir), HashingEmbedder(), ingested)
    return evaluate(r, DATASET, min_confidence=ingested.min_confidence, k=4)


def test_retrieval_recall_and_ranking(report):
    print("\nRAG eval:", {k: v for k, v in report.items() if k != "failures"})
    assert report["recall_at_k"] >= 0.9, report["failures"]
    assert report["mrr"] >= 0.85


def test_retrieved_context_contains_expected_facts(report):
    assert report["fact_coverage"] >= 0.9


def test_supported_questions_clear_the_grounding_threshold(report):
    assert report["grounded_rate"] >= 0.9


def test_unsupported_questions_are_rejected(report):
    assert report["unsupported_reject"] >= 0.8, report["failures"]
