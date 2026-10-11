"""Cross-regulation intelligence.

Finds similar obligations across regulations, shared control coverage,
duplicate/overlapping requirements, reuse opportunities and conflicting
deadlines. Similarity is **never** used to merge legally distinct obligations —
results are reported as candidates for review, with the differing scope,
jurisdiction and effective window stated.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models.core import MappingStatus, ReviewStatus
from app.models.obligations import Obligation
from app.services.mapping import _keywords, _expand

log = get_logger("regulens.crossregulation")

SIMILARITY_FLOOR = 0.62


@dataclass
class ObligationPair:
    left_id: str
    right_id: str
    similarity: float
    shared_terms: list[str]
    same_regulation: bool
    scope_differences: list[str]

    @property
    def merge_candidate(self) -> bool:
        return not self.scope_differences


@dataclass
class CrossRegulationReport:
    pairs: list[ObligationPair]
    shared_controls: list[dict[str, Any]]
    overlapping_requirements: list[dict[str, Any]]
    reuse_opportunities: list[dict[str, Any]]
    deadline_conflicts: list[dict[str, Any]]
    distinct_but_similar: list[dict[str, Any]]


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _lexical_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = a & b
    union = a | b
    return len(inter) / len(union)


def _scope_differences(left: Obligation, right: Obligation) -> list[str]:
    diffs: list[str] = []
    if left.regulation_id != right.regulation_id:
        diffs.append("different regulation")
    if (left.jurisdiction or "") != (right.jurisdiction or ""):
        diffs.append(f"different jurisdiction ({left.jurisdiction} vs {right.jurisdiction})")
    if left.effective_date != right.effective_date:
        diffs.append(f"different effective date ({left.effective_date} vs {right.effective_date})")
    if (left.deadline_date or None) != (right.deadline_date or None):
        diffs.append(f"different deadline ({left.deadline_date} vs {right.deadline_date})")
    if left.regulation_version_id != right.regulation_version_id:
        diffs.append("different regulation version")
    return diffs


def find_similar_obligations(
    session: Session, *, floor: float = SIMILARITY_FLOOR, limit: int = 40
) -> list[ObligationPair]:
    obligations = list(
        session.scalars(select(Obligation).where(Obligation.review_status == ReviewStatus.APPROVED).limit(2000))
    )
    keyword_cache = {o.id: _expand(_keywords(f"{o.obligation_text} {o.quoted_text}")) for o in obligations}
    pairs: list[ObligationPair] = []
    for i, left in enumerate(obligations):
        for right in obligations[i + 1 :]:
            seq = _similarity(f"{left.obligation_text} {left.quoted_text}", f"{right.obligation_text} {right.quoted_text}")
            lex = _lexical_jaccard(keyword_cache[left.id], keyword_cache[right.id])
            score = round(max(seq, lex), 3)
            if score < floor:
                continue
            pairs.append(
                ObligationPair(
                    left_id=left.id,
                    right_id=right.id,
                    similarity=score,
                    shared_terms=sorted(keyword_cache[left.id] & keyword_cache[right.id])[:15],
                    same_regulation=left.regulation_id == right.regulation_id,
                    scope_differences=_scope_differences(left, right),
                )
            )
    pairs.sort(key=lambda p: p.similarity, reverse=True)
    return pairs[:limit]


def build_report(session: Session, *, floor: float = SIMILARITY_FLOOR) -> CrossRegulationReport:
    pairs = find_similar_obligations(session, floor=floor)
    obligations = {o.id: o for o in session.scalars(select(Obligation)).all()}

    shared_controls: list[dict[str, Any]] = []
    control_to_obligations: dict[str, set[str]] = {}
    control_to_regulations: dict[str, set[str]] = {}
    from app.models.controls import ObligationControlMapping

    for mapping in session.scalars(select(ObligationControlMapping)).all():
        if mapping.status != MappingStatus.APPROVED:
            continue
        control_to_obligations.setdefault(mapping.control_id, set()).add(mapping.obligation_id)
    for control_id, obl_ids in control_to_obligations.items():
        regs = {obligations[o].regulation_id for o in obl_ids if o in obligations}
        if len(regs) > 1:
            shared_controls.append(
                {
                    "control_id": control_id,
                    "obligation_ids": sorted(obl_ids),
                    "regulation_ids": sorted(regs),
                    "coverage_count": len(obl_ids),
                    "note": "This control is mapped to obligations from more than one regulation. "
                    "Verify that the legal scope genuinely overlaps before treating it as shared coverage.",
                }
            )

    overlapping: list[dict[str, Any]] = []
    distinct_but_similar: list[dict[str, Any]] = []
    for pair in pairs:
        left, right = obligations.get(pair.left_id), obligations.get(pair.right_id)
        if left is None or right is None:
            continue
        if pair.same_regulation:
            overlapping.append(
                {
                    "left": left.obligation_uid,
                    "right": right.obligation_uid,
                    "similarity": pair.similarity,
                    "shared_terms": pair.shared_terms,
                    "note": "Two obligations within the same regulation look similar — possible duplication.",
                }
            )
        if pair.scope_differences:
            distinct_but_similar.append(
                {
                    "left": left.obligation_uid,
                    "right": right.obligation_uid,
                    "similarity": pair.similarity,
                    "scope_differences": pair.scope_differences,
                    "left_text": left.obligation_text[:300],
                    "right_text": right.obligation_text[:300],
                    "note": "Textually similar but legally distinct scope. NOT merged.",
                }
            )

    reuse = [
        entry
        for entry in shared_controls
        if entry["coverage_count"] > 1
    ]

    deadline_conflicts: list[dict[str, Any]] = []
    for pair in pairs:
        left, right = obligations.get(pair.left_id), obligations.get(pair.right_id)
        if left is None or right is None:
            continue
        if left.deadline_date and right.deadline_date and left.deadline_date != right.deadline_date:
            deadline_conflicts.append(
                {
                    "left": left.obligation_uid,
                    "right": right.obligation_uid,
                    "left_deadline": left.deadline_date.isoformat(),
                    "right_deadline": right.deadline_date.isoformat(),
                    "note": "Similar obligations carry different deadlines. Confirm which governs.",
                }
            )

    return CrossRegulationReport(
        pairs=pairs,
        shared_controls=shared_controls,
        overlapping_requirements=overlapping,
        reuse_opportunities=reuse,
        deadline_conflicts=deadline_conflicts,
        distinct_but_similar=distinct_but_similar,
    )


def report_to_json(report: CrossRegulationReport) -> dict[str, Any]:
    return {
        "similar_pairs": [
            {
                "left": p.left_id,
                "right": p.right_id,
                "similarity": p.similarity,
                "shared_terms": p.shared_terms,
                "same_regulation": p.same_regulation,
                "scope_differences": p.scope_differences,
                "merge_candidate": p.merge_candidate,
            }
            for p in report.pairs
        ],
        "shared_controls": report.shared_controls,
        "overlapping_requirements": report.overlapping_requirements,
        "reuse_opportunities": report.reuse_opportunities,
        "deadline_conflicts": report.deadline_conflicts,
        "distinct_but_similar": report.distinct_but_similar,
        "note": "No obligations were merged. Similarity is an advisory signal only.",
    }


__all__ = ["find_similar_obligations", "build_report", "report_to_json", "CrossRegulationReport"]
