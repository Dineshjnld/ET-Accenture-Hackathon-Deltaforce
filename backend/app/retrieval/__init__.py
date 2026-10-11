"""Retrieval abstraction (hybrid lexical + optional semantic)."""
from __future__ import annotations

from app.retrieval.hybrid import RetrievalHit, RetrievalResult, Retriever, semantic_search

__all__ = ["Retriever", "RetrievalHit", "RetrievalResult", "semantic_search"]
