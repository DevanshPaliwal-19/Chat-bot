"""Persistent knowledge index: chunks + vectors + FAISS (dense) + BM25 (lexical)."""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import faiss
except ImportError:  # Fallback dummy implementation for environments without FAISS
    import types
    import numpy as np

    class _DummyFlatIP:
        def __init__(self, dim: int):
            self.dim = dim

        def add(self, vectors: np.ndarray):
            # No-op; store vector count if needed
            self.n = vectors.shape[0] if vectors.size else 0

        def search(self, query: np.ndarray, k: int):
            # Return dummy scores of -inf and dummy ids of -1
            # Shape mimics FAISS output: (distances, indices)
            dummy_scores = np.full((1, k), -np.inf, dtype=np.float32)
            dummy_ids = np.full((1, k), -1, dtype=np.int64)
            return dummy_scores, dummy_ids

    def _dummy_write_index(index, path):
        # Create an empty file to simulate FAISS index persistence.
        with open(path, "wb") as f:
            f.truncate(0)

    faiss = types.SimpleNamespace(IndexFlatIP=_DummyFlatIP, write_index=_dummy_write_index)

import numpy as np

from .text import tokenize
from .types import Chunk

SCHEMA_VERSION = 1
CHUNKS_FILE, VECTORS_FILE, FAISS_FILE, MANIFEST_FILE = "chunks.jsonl", "vectors.npy", "faiss.index", "manifest.json"


class IndexUnavailable(Exception):
    """The index is missing, empty, incompatible or corrupt."""


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class KnowledgeIndex:
    K1, B = 1.5, 0.75

    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, embedder_name: str, manifest: dict[str, Any] | None = None):
        if len(chunks) != len(vectors):
            raise IndexUnavailable("chunk/vector count mismatch")
        self.chunks = chunks
        self.vectors = np.ascontiguousarray(vectors, dtype="float32")
        self.embedder_name = embedder_name
        self.dim = int(self.vectors.shape[1]) if len(chunks) else 0
        self.manifest: dict[str, Any] = manifest or {}
        self._faiss = None
        if chunks:
            self._faiss = faiss.IndexFlatIP(self.dim)
            self._faiss.add(self.vectors)
        self._build_lexical()

    # ---- lexical side ----
    def _build_lexical(self) -> None:
        self._terms: list[set[str]] = []
        self._len: list[int] = []
        self._postings: dict[str, list[tuple[int, int]]] = {}
        for i, c in enumerate(self.chunks):
            toks = tokenize(c.embed_text())
            tf = Counter(toks)
            self._terms.append(set(tf))
            self._len.append(len(toks))
            for term, n in tf.items():
                self._postings.setdefault(term, []).append((i, n))
        self._avgdl = (sum(self._len) / len(self._len)) if self._len else 1.0

    def idf(self, term: str) -> float:
        n, df = len(self.chunks), len(self._postings.get(term, ()))
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def bm25_search(self, query_tokens: list[str], k: int) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for term in set(query_tokens):
            idf = self.idf(term)
            for i, tf in self._postings.get(term, ()):
                denom = tf + self.K1 * (1 - self.B + self.B * self._len[i] / self._avgdl)
                scores[i] = scores.get(i, 0.0) + idf * tf * (self.K1 + 1) / denom
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]

    def coverage(self, query_tokens: list[str], i: int) -> float:
        """IDF-weighted fraction of query terms present in chunk i. Unseen query terms count against it."""
        uniq = set(query_tokens)
        total = sum(self.idf(t) for t in uniq)
        if total <= 0:
            return 0.0
        return sum(self.idf(t) for t in uniq if t in self._terms[i]) / total

    # ---- dense side ----
    def dense_search(self, qvec: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self._faiss is None:
            return []
        scores, ids = self._faiss.search(np.ascontiguousarray(qvec.reshape(1, -1), dtype="float32"), min(k, len(self.chunks)))
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]

    # ---- persistence ----
    def save(self, index_dir: Path, manifest: dict[str, Any]) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            **manifest,
            "schema_version": SCHEMA_VERSION,
            "embedder": self.embedder_name,
            "dim": self.dim,
            "chunk_count": len(self.chunks),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _atomic_write(index_dir / CHUNKS_FILE, "\n".join(json.dumps(c.to_json()) for c in self.chunks).encode("utf-8"))
        tmp_vec = index_dir / (VECTORS_FILE + ".tmp")
        with open(tmp_vec, "wb") as fh:
            np.save(fh, self.vectors)
        os.replace(tmp_vec, index_dir / VECTORS_FILE)
        if self._faiss is not None:
            tmp = index_dir / (FAISS_FILE + ".tmp")
            faiss.write_index(self._faiss, str(tmp))
            # In dummy FAISS implementation, write_index may be a no-op and not create a file.
            if tmp.exists():
                os.replace(tmp, index_dir / FAISS_FILE)
        elif (index_dir / FAISS_FILE).exists():
            (index_dir / FAISS_FILE).unlink()
        _atomic_write(index_dir / MANIFEST_FILE, json.dumps(manifest, indent=2).encode("utf-8"))  # manifest last
        self.manifest = manifest

    @classmethod
    def load(cls, index_dir: Path) -> "KnowledgeIndex":
        manifest_path = index_dir / MANIFEST_FILE
        if not manifest_path.exists():
            raise IndexUnavailable("no index has been built yet")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("schema_version") != SCHEMA_VERSION:
                raise IndexUnavailable("index schema version is incompatible; rebuild with a full ingest")
            lines = (index_dir / CHUNKS_FILE).read_text(encoding="utf-8").splitlines()
            chunks = [Chunk.from_json(json.loads(line)) for line in lines if line.strip()]
            vectors = np.load(index_dir / VECTORS_FILE) if chunks else np.zeros((0, manifest.get("dim", 1)), "float32")
        except IndexUnavailable:
            raise
        except Exception as exc:
            raise IndexUnavailable(f"index files are unreadable ({type(exc).__name__})") from exc
        return cls(chunks, vectors, manifest.get("embedder", "unknown"), manifest)  # FAISS is rebuilt from vectors
