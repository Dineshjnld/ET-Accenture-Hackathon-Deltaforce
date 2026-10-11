"""Hybrid retrieval: lexical (BM25-lite in Python) + optional exact vector ranking.

Vector search is a *soft* dependency. Embeddings are stored portably
(JSON/TEXT) and ranked with exact cosine similarity in Python whenever an
embedding provider is configured. The pgvector extension is enabled
opportunistically but is NOT required and no ANN behaviour is claimed.
Callers can always ask ``Retriever.capabilities()`` to know exactly what is
being used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
from sqlalchemy import Select, and_, or_, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import pgvector_enabled
from app.llm.embeddings import cosine, get_embedder, tokenize
from app.logging_config import get_logger
from app.models.regulatory import DocumentChunk

log = get_logger("regulens.retrieval")


@dataclass
class RetrievalHit:
    chunk_id: str
    document_id: str
    score: float
    lexical_score: float
    vector_score: float
    text: str
    page: int | None
    section: str | None
    heading: str | None
    citation: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    hits: list[RetrievalHit]
    mode: str
    total_candidates: int
    notes: list[str] = field(default_factory=list)


class Retriever:
    """Hybrid retriever over document chunks."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    def capabilities(self) -> dict[str, Any]:
        emb = get_embedder().status()
        return {
            "lexical": True,
            "lexical_engine": "python_bm25_lite",
            "vector_ranking": bool(emb.get("available")),
            "vector_engine": "python_cosine_exact",
            "pgvector_extension": pgvector_enabled(),
            "pgvector_note": "Extension is enabled opportunistically; ranking uses exact "
            "cosine in Python (correct at this scale; ANN index is future work).",
            "embeddings": emb,
            "top_k_default": settings.RETRIEVAL_TOP_K,
        }

    # ------------------------------------------------------------------
    def _lexical_terms(self, query: str) -> list[str]:
        return tokenize(query)

    def _lexical_search(self, query: str, top_k: int, filters: list[Any]) -> list[tuple[DocumentChunk, float]]:
        terms = self._lexical_terms(query)
        if not terms:
            return []
        docs = self.session.scalars(self._base_query(filters)).all()
        if not docs:
            return []
        # BM25-lite: idf-weighted term frequency over the candidate corpus.
        df: dict[str, int] = {}
        doc_terms: list[set[str]] = []
        for doc in docs:
            toks = set(tokenize(doc.text))
            doc_terms.append(toks)
            for t in toks:
                df[t] = df.get(t, 0) + 1
        n = len(docs)
        idf = {t: np.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)) for t in terms}
        avg_len = sum(len(tokenize(d.text)) for d in docs) / n
        k1, b = 1.5, 0.75
        scored: list[tuple[DocumentChunk, float]] = []
        for doc, toks in zip(docs, doc_terms):
            raw = tokenize(doc.text)
            length = len(raw) or 1
            tf = {t: raw.count(t) for t in terms}
            score = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f == 0:
                    continue
                score += idf[t] * (f * (k1 + 1)) / (f + k1 * (1 - b + b * length / (avg_len or 1)))
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_k]
        if not top:
            return []
        best = top[0][1] or 1.0
        return [(doc, min(1.0, score / best)) for doc, score in top]

    # ------------------------------------------------------------------
    def _vector_search(self, query: str, top_k: int, filters: list[Any]) -> tuple[list[tuple[DocumentChunk, float]], str]:
        embedder = get_embedder()
        status = embedder.status()
        if not status.get("available"):
            return [], "no_embedding_provider"
        try:
            qvec = embedder.embed([query])[0]
        except Exception as exc:
            log.warning("retrieval.embedding_failed", error=str(exc))
            return [], "embedding_failed"
        docs = self.session.scalars(self._base_query(filters)).all()
        scored: list[tuple[DocumentChunk, float]] = []
        for doc in docs:
            if not doc.embedding:
                continue
            scored.append((doc, cosine(qvec, doc.embedding)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(d, s) for d, s in scored[:top_k] if s > 0], "python_cosine_exact"

    # ------------------------------------------------------------------
    def _base_query(self, filters: Iterable[Any]) -> Select:
        stmt = select(DocumentChunk)
        for f in filters:
            stmt = stmt.where(f)
        return stmt.order_by(DocumentChunk.id).limit(settings.RETRIEVAL_MAX_CANDIDATES)

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        source_kind: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        use_vectors: bool = True,
    ) -> RetrievalResult:
        top_k = top_k or settings.RETRIEVAL_TOP_K
        filters: list[Any] = []
        if document_ids:
            filters.append(DocumentChunk.document_id.in_(document_ids))
        if source_kind:
            filters.append(
                DocumentChunk.document_id.in_(
                    select(_doc_id_col()).where(_doc_source_kind_col() == source_kind)
                )
            )
        notes: list[str] = []

        lexical = dict(self._lexical_search(query, top_k, filters))
        vector: dict[str, float] = {}
        mode = "lexical_only"
        if use_vectors:
            vec_hits, vec_mode = self._vector_search(query, top_k, filters)
            vector = {doc.id: score for doc, score in vec_hits}
            if vec_mode == "python_cosine_exact":
                mode = "hybrid"
            else:
                notes.append(
                    "No embedding provider configured — semantic ranking unavailable; "
                    "lexical results only."
                )

        candidate_ids = list(dict.fromkeys([*lexical.keys(), *vector.keys()]))
        if not candidate_ids:
            return RetrievalResult(hits=[], mode=mode, total_candidates=0, notes=notes)

        chunks = {
            c.id: c
            for c in self.session.scalars(
                self._base_query(filters).where(DocumentChunk.id.in_(candidate_ids))
            ).all()
        }
        alpha = 0.6 if vector else 1.0
        beta = 0.4 if vector else 0.0
        fused: list[tuple[str, float, float, float]] = []
        for cid in candidate_ids:
            lex = lexical.get(cid, 0.0)
            vec = vector.get(cid, 0.0)
            score = alpha * lex + beta * vec
            if score < settings.SIMILARITY_MIN_SCORE:
                continue
            fused.append((cid, score, lex, vec))
        fused.sort(key=lambda x: x[1], reverse=True)

        hits: list[RetrievalHit] = []
        for cid, score, lex, vec in fused[:top_k]:
            chunk = chunks[cid]
            doc = chunk.document
            hits.append(
                RetrievalHit(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    score=round(score, 4),
                    lexical_score=round(lex, 4),
                    vector_score=round(vec, 4),
                    text=chunk.text,
                    page=chunk.page,
                    section=chunk.section,
                    heading=chunk.heading,
                    citation={
                        "document_id": doc.id if doc else None,
                        "stored_filename": doc.stored_filename if doc else None,
                        "filename": doc.filename if doc else None,
                        "source_url": doc.source_url if doc else None,
                        "page": chunk.page,
                        "section": chunk.section,
                        "source_kind": str(doc.source_kind) if doc else None,
                    },
                )
            )
        return RetrievalResult(hits=hits, mode=mode, total_candidates=len(candidate_ids), notes=notes)


def _doc_id_col() -> Any:
    from app.models.regulatory import Document

    return Document.id


def _doc_source_kind_col() -> Any:
    from app.models.regulatory import Document

    return Document.source_kind


def semantic_search(session: Session, query: str, **kwargs: Any) -> RetrievalResult:
    return Retriever(session).search(query, **kwargs)


__all__ = ["Retriever", "RetrievalHit", "RetrievalResult", "semantic_search"]
