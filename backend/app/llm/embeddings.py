"""Deterministic lexical embeddings (hashed TF-IDF, CPU-only).

No hosted or neural embedding backend is configured. Vector ranking in
retrieval is exact cosine over these lexical vectors in Python and is always
labelled ``lexical`` — never presented as neural semantic understanding.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

import numpy as np

from app.config import settings
from app.logging_config import get_logger

log = get_logger("regulens.embeddings")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class EmbeddingProvider(ABC):
    kind = "abstract"

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def status(self) -> dict[str, Any]: ...


class TfidfEmbeddingProvider(EmbeddingProvider):
    """Deterministic lexical embeddings. Labelled as lexical, never as semantic."""

    kind = "lexical_tfidf"

    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension or settings.EMBEDDING_DIMENSION

    def embed(self, texts: list[str]) -> list[list[float]]:
        matrix = np.zeros((len(texts), self.dimension), dtype=float)
        for i, text in enumerate(texts):
            counts = Counter(tokenize(text))
            if not counts:
                continue
            norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
            for token, count in counts.items():
                bucket = (
                    int(hashlib.blake2b(token.encode("utf-8"), digest_size=4).hexdigest(), 16)
                    % self.dimension
                )
                sign = -1.0 if (bucket % 2) else 1.0
                matrix[i, bucket] += sign * (count / norm)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return [row.tolist() for row in (matrix / norms)]

    def status(self) -> dict[str, Any]:
        return {
            "provider": "tfidf",
            "kind": "lexical",
            "note": "Hashed lexical TF-IDF vectors. NOT neural semantic embeddings.",
            "available": True,
        }


class NullEmbeddingProvider(EmbeddingProvider):
    kind = "none"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("No embedding provider configured")

    def status(self) -> dict[str, Any]:
        return {"provider": "none", "kind": "none", "available": False, "reason": "embeddings disabled"}


_provider: EmbeddingProvider | None = None


def get_embedder() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = TfidfEmbeddingProvider()
    return _provider


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def embedding_status() -> dict[str, Any]:
    return get_embedder().status()


__all__ = [
    "EmbeddingProvider",
    "TfidfEmbeddingProvider",
    "NullEmbeddingProvider",
    "get_embedder",
    "cosine",
    "tokenize",
]
