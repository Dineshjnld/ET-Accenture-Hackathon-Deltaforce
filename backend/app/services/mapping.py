"""Obligation → control mapping (hybrid retrieval + validation).

Candidate mappings are produced by fused lexical (BM25-style over the
obligation/control corpus) and — when an embedding provider is configured —
vector similarity, then validated by explicit rules. Nothing is silently
promoted: every mapping starts as ``candidate`` and requires a human decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.embeddings import cosine, get_embedder, tokenize
from app.logging_config import get_logger
from app.models.core import MappingStatus
from app.models.controls import Control, ObligationControlMapping
from app.models.obligations import Obligation

log = get_logger("regulens.mapping")

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "by", "with",
    "shall", "must", "should", "may", "any", "all", "be", "is", "are", "as",
    "that", "this", "it", "its", "from", "at", "or", "if", "not", "no", "per",
    "every", "each", "least", "such", "more", "than", "most", "other", "same",
    "both", "either", "neither", "whether", "while", "within", "without",
    "upon", "into", "via", "one", "two", "three", "first", "second",
}

# Domain synonyms used to bridge regulatory wording and internal control wording.
_SYNONYMS: dict[str, list[str]] = {
    "ombudsman": ["internal ombudsman", "grievance", "complaint", "redressal", "io"],
    "complaint": ["grievance", "redressal"],
    "grievance": ["complaint", "redressal"],
    "kyc": ["know your customer", "customer due diligence", "cdd"],
    "aml": ["anti money laundering", "cft"],
    "reporting": ["return", "submission", "report"],
    "disclosure": ["disclose", "publish", "display"],
    "appointment": ["appoint", "nomination", "engage"],
    "training": ["awareness", "capacity building"],
    "monitoring": ["oversight", "surveillance", "review"],
}


@dataclass
class MappingCandidate:
    control_id: str
    confidence: float
    rationale: str
    lexical_score: float
    vector_score: float
    shared_terms: list[str] = field(default_factory=list)
    proposed_by: str = "rule"
    proposed_model: str | None = None
    source_references: list[dict[str, Any]] = field(default_factory=list)


def _keywords(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in _STOPWORDS and len(t) > 1}


def _expand(keywords: set[str]) -> set[str]:
    expanded = set(keywords)
    for kw in keywords:
        for syn in _SYNONYMS.get(kw, []):
            expanded.update(tokenize(syn))
    return expanded


def _control_text(control: Control) -> str:
    parts = [
        control.title or "",
        control.control_objective or "",
        control.control_activity or "",
        control.expected_evidence or "",
        control.testing_procedure or "",
        control.owner_role or "",
    ]
    return " ".join(p for p in parts if p)


def lexical_overlap_score(obligation_keywords: set[str], control_keywords: set[str]) -> float:
    if not obligation_keywords or not control_keywords:
        return 0.0
    expanded = _expand(obligation_keywords)
    inter = expanded & control_keywords
    if not inter:
        return 0.0
    # overlap coefficient, tolerant to long control descriptions
    denom = min(len(expanded), len(control_keywords)) or 1
    return min(1.0, len(inter) / denom)


def _bm25_idf_chunks(chunks: list[str]) -> dict[str, float]:
    import math

    df: dict[str, int] = {}
    for chunk in chunks:
        for term in set(tokenize(chunk)):
            df[term] = df.get(term, 0) + 1
    n = len(chunks)
    return {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}


def suggest_mappings(
    session: Session,
    obligation: Obligation,
    *,
    controls: list[Control] | None = None,
    top_k: int = 5,
    use_vectors: bool = True,
    min_confidence: float | None = None,
    business_unit_id: str | None = None,
) -> list[MappingCandidate]:
    """Return ranked candidate mappings for an obligation."""
    min_confidence = settings.MAPPING_MIN_CONFIDENCE if min_confidence is None else min_confidence
    if controls is None:
        stmt = select(Control).where(Control.status != MappingStatus.REJECTED)
        if business_unit_id:
            stmt = stmt.where((Control.business_unit_id == business_unit_id) | (Control.business_unit_id.is_(None)))
        controls = list(session.scalars(stmt))
    if not controls:
        return []

    obligation_text = f"{obligation.obligation_text} {obligation.quoted_text}"
    obl_keywords = _keywords(obligation_text)
    # NOTE: keyed by position, not by ``control.id`` — transient (unpersisted)
    # controls share ``id=None`` and must still score independently.
    control_texts = [_control_text(c) for c in controls]
    control_keywords = [_keywords(t) for t in control_texts]
    idf = _bm25_idf_chunks([*control_texts, obligation_text])

    # vector similarity (only when an embedding provider is available).
    # Keyed by position so transient controls (id=None) still score independently.
    vector_scores: dict[int, float] = {}
    embedder = get_embedder()
    emb_status = embedder.status()
    if use_vectors and emb_status.get("available"):
        try:
            obl_vec = embedder.embed([obligation_text[:8000]])[0]
            vecs = embedder.embed([t[:8000] for t in control_texts])
            for idx, vec in enumerate(vecs):
                vector_scores[idx] = cosine(obl_vec, vec)
        except Exception as exc:
            log.warning("mapping.embedding_failed", error=str(exc))

    candidates: list[MappingCandidate] = []
    for idx, control in enumerate(controls):
        ck = control_keywords[idx]
        base = lexical_overlap_score(obl_keywords, ck)
        weighted = 0.0
        if idf and obl_keywords:
            hits = (_expand(obl_keywords) & ck)
            total = sum(idf.get(t, 0.0) for t in _expand(obl_keywords)) or 1.0
            weighted = min(1.0, sum(idf.get(t, 0.0) for t in hits) / total)
        vector = vector_scores.get(idx, 0.0)
        score = 0.45 * base + 0.35 * weighted + (0.20 * max(0.0, vector) if vector_scores else 0.0)
        if score < min_confidence:
            continue
        shared = sorted(_expand(obl_keywords) & ck)[:12]
        candidates.append(
            MappingCandidate(
                control_id=control.id,
                confidence=round(min(0.95, score), 3),
                rationale=_rationale(control, shared, base, weighted, vector, bool(vector_scores)),
                lexical_score=round(base, 4),
                vector_score=round(vector, 4),
                shared_terms=shared,
                proposed_by="hybrid" if vector_scores else "rule",
                proposed_model=(f"{emb_status.get('provider')}:{emb_status.get('model')}" if vector_scores else None),
                source_references=_control_references(control),
            )
        )
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates[:top_k]


def _rationale(
    control: Control,
    shared: list[str],
    lexical: float,
    weighted: float,
    vector: float,
    used_vectors: bool,
) -> str:
    terms = ", ".join(shared[:8]) or "none"
    signal = "lexical"
    if used_vectors and vector > 0:
        signal = "lexical + vector"
    return (
        f"{signal} match between the obligation and control '{control.control_uid}' "
        f"(shared terms: {terms}; lexical={lexical:.2f}, idf-weighted={weighted:.2f}"
        + (f", vector={vector:.2f}" if used_vectors else "")
        + "). This is a candidate for human review, not an approved mapping."
    )


def _control_references(control: Control) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = [
        {"type": "control", "id": control.id, "uid": control.control_uid, "title": control.title}
    ]
    if control.source_document_id:
        refs.append({"type": "document", "id": control.source_document_id})
    if control.policy_version_id:
        refs.append({"type": "policy_version", "id": control.policy_version_id})
    return refs


def persist_candidates(
    session: Session,
    obligation: Obligation,
    candidates: list[MappingCandidate],
    *,
    actor: str = "mapping_agent",
) -> list[ObligationControlMapping]:
    created: list[ObligationControlMapping] = []
    existing = {
        (m.control_id, m.status)
        for m in session.scalars(
            select(ObligationControlMapping).where(
                ObligationControlMapping.obligation_id == obligation.id
            )
        )
    }
    for cand in candidates:
        if (cand.control_id, MappingStatus.CANDIDATE) in existing:
            continue
        if (cand.control_id, MappingStatus.APPROVED) in existing:
            continue
        mapping = ObligationControlMapping(
            obligation_id=obligation.id,
            control_id=cand.control_id,
            mapping_rationale=cand.rationale,
            source_references=_json(cand.source_references),
            match_signals=_json(
                {
                    "lexical": cand.lexical_score,
                    "idf_weighted": None,
                    "vector": cand.vector_score if cand.proposed_model else None,
                    "shared_terms": cand.shared_terms,
                    "fused": cand.confidence,
                }
            ),
            confidence=cand.confidence,
            status=MappingStatus.CANDIDATE,
            proposed_by=cand.proposed_by,
            proposed_model=cand.proposed_model,
        )
        session.add(mapping)
        created.append(mapping)
    session.flush()
    log.info(
        "mapping.candidates_created",
        obligation=obligation.obligation_uid,
        count=len(created),
        actor=actor,
    )
    return created


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


__all__ = [
    "suggest_mappings",
    "persist_candidates",
    "lexical_overlap_score",
    "MappingCandidate",
]
