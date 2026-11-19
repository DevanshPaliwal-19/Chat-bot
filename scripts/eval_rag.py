r"""Evaluate retrieval quality on your own question set.

    python scripts/eval_rag.py path\to\my_eval.json [--k 4] [--min-confidence 0.30] [--sweep]

Dataset: a JSON list of items
    {"id": "s1", "type": "supported",   "question": "...", "expected_docs": ["relative/path.pdf"], "expected_facts": ["phrase"]}
    {"id": "u1", "type": "unsupported", "question": "something your documents do NOT cover"}
`expected_docs` are paths relative to the knowledge base directory. Write the questions from your REAL documents;
the sample in tests/eval/rag_eval.json is for a fictional company and only exercises the code.
--sweep prints the grounded-rate / unsupported-reject trade-off for several thresholds so you can tune OSIYA_MIN_CONFIDENCE.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.rag.embeddings import build_embedder  # noqa: E402
from app.rag.evaluation import evaluate  # noqa: E402
from app.rag.index import IndexUnavailable, KnowledgeIndex  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--min-confidence", type=float, default=None)
    p.add_argument("--sweep", action="store_true")
    a = p.parse_args()

    s = get_settings()
    try:
        idx = KnowledgeIndex.load(s.index_dir)
    except IndexUnavailable as exc:
        print(f"No usable index ({exc}). Run first: python -m app.rag.cli ingest")
        return 2
    embedder = build_embedder(s)
    print(f"embedder={idx.manifest.get('embedder')} chunks={idx.manifest.get('chunk_count')}")
    data = json.loads(a.dataset.read_text(encoding="utf-8"))
    retriever = Retriever(idx, embedder, s)

    thresholds = [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5] if a.sweep else [a.min_confidence or s.min_confidence]
    report = None
    for t in thresholds:
        report = evaluate(retriever, data, min_confidence=t, k=a.k)
        print(f"threshold={t:.2f} recall@{a.k}={report['recall_at_k']} mrr={report['mrr']} fact_coverage={report['fact_coverage']} "
              f"grounded_rate={report['grounded_rate']} unsupported_reject={report['unsupported_reject']}")
    if report and report["failures"]:
        print("\nFailures at the last threshold:")
        for f in report["failures"]:
            print("  ", json.dumps(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
