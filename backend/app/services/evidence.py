"""Evidence state helpers used by gap detection and risk scoring."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.assessment import assess_evidence


def evidence_state_for_control(session: Session, control_id: str | None) -> dict[str, Any] | None:
    if not control_id:
        return None
    from app.models.evidence import Evidence, EvidenceControlLink

    links = list(session.scalars(select(EvidenceControlLink).where(EvidenceControlLink.control_id == control_id)))
    if not links:
        return {"total": 0, "sufficient": 0, "insufficient": 0, "stale": 0, "missing": 0, "coverage": 0.0}
    items = list(session.scalars(select(Evidence).where(Evidence.id.in_([l.evidence_id for l in links]))))
    assessments = [assess_evidence(e) for e in items]
    counts = {"sufficient": 0, "insufficient": 0, "stale": 0, "missing": 0, "inconclusive": 0, "not_reviewed": 0}
    for a in assessments:
        counts[a.validity.value] = counts.get(a.validity.value, 0) + 1
    return {
        "total": len(assessments),
        "sufficient": counts["sufficient"],
        "insufficient": counts["insufficient"] + counts["inconclusive"],
        "stale": counts["stale"],
        "missing": counts["missing"] + counts["not_reviewed"],
        "coverage": round(counts["sufficient"] / len(assessments), 4) if assessments else 0.0,
    }
