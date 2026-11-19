"""Command line for the knowledge base.

    python -m app.rag.cli ingest [--full]
    python -m app.rag.cli status
    python -m app.rag.cli search "your question"
"""
from __future__ import annotations

import argparse
import json
import sys

from ..config import get_settings
from ..logging_setup import configure_logging
from .embeddings import build_embedder
from .index import IndexUnavailable, KnowledgeIndex
from .ingest import Ingestor
from .retriever import Retriever


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="app.rag.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="scan the knowledge base and update the index")
    ing.add_argument("--full", action="store_true", help="ignore the existing index and rebuild")
    sub.add_parser("status", help="show index status")
    srch = sub.add_parser("search", help="run a retrieval query and print scores")
    srch.add_argument("query")
    args = p.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    if args.cmd == "status":
        try:
            idx = KnowledgeIndex.load(settings.index_dir)
        except IndexUnavailable as exc:
            print(f"No usable index: {exc}")
            return 1
        m = idx.manifest
        print(json.dumps({k: m.get(k) for k in ("index_version", "embedder", "chunk_count", "built_at")}, indent=2))
        print(f"documents: {len(m.get('documents', {}))}")
        return 0

    embedder = build_embedder(settings)
    if args.cmd == "ingest":
        report = Ingestor(settings, embedder).run(full_rebuild=args.full)
        print(json.dumps(report.to_dict(), indent=2))
        return 1 if report.errors and not report.chunk_count else 0

    try:
        idx = KnowledgeIndex.load(settings.index_dir)
    except IndexUnavailable as exc:
        print(f"No usable index: {exc}. Run: python -m app.rag.cli ingest")
        return 1
    result = Retriever(idx, embedder, settings).retrieve(args.query)
    print(f"confidence={result.confidence:.3f} (threshold {settings.min_confidence}) status={result.status}")
    for r in result.chunks:
        c = r.chunk
        print(f"- {r.score:.3f} dense={r.dense:.3f} cov={r.coverage:.3f} {c.source_document} p={c.page_number} :: {c.text[:90]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
