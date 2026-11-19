"""Embedding providers behind one small interface.

`SentenceTransformerEmbedder` gives real semantic vectors. `HashingEmbedder` is a dependency-free,
deterministic fallback (lexical, not semantic) used when sentence-transformers is not available and in tests.
"""
from __future__ import annotations

import logging
import math
import zlib
from typing import Protocol

import numpy as np

from ..config import Settings
from .text import tokenize

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _l2_normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype("float32")


class HashingEmbedder:
    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for row, text in enumerate(texts):
            toks = tokenize(text)
            feats: dict[str, float] = {}
            for t in toks:
                feats[t] = feats.get(t, 0.0) + 1.0
            for a, b in zip(toks, toks[1:]):
                feats[f"{a}_{b}"] = feats.get(f"{a}_{b}", 0.0) + 0.5
            for f, count in feats.items():
                h = zlib.crc32(f.encode("utf-8"))
                sign = 1.0 if (zlib.crc32(b"s" + f.encode("utf-8")) & 1) else -1.0
                out[row, h % self.dim] += sign * (1.0 + math.log(count))
        return _l2_normalize(out)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # heavy import, kept lazy

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = f"st:{model_name}"

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, batch_size=32, show_progress_bar=False, convert_to_numpy=True)
        return _l2_normalize(np.asarray(vecs, dtype="float32"))


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "hashing":
        return HashingEmbedder()
    try:
        return SentenceTransformerEmbedder(settings.embedding_model)
    except Exception as exc:
        if settings.embedding_provider == "sentence-transformers":
            raise
        logger.warning(
            "sentence-transformers unavailable (%s); falling back to the lexical hashing embedder. "
            "Retrieval quality will be lower.",
            type(exc).__name__,
        )
        return HashingEmbedder()
