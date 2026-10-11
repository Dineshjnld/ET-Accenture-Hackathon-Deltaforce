"""Autonomous regulatory impact analysis with dependency-aware propagation.

Traversal:
    change
      └─> affected obligations            (clause-ref match, then text similarity)
            └─> approved mappings        (obligation_control_mappings, approved only)
                  └─> controls
                        └─> evidence & tests
                              └─> gaps
                                    └─> remediation actions

The propagation path is persisted so a reviewer can replay exactly how the
impact assessment was reached. Relationships below the confidence floor are
recorded in ``uncertainty_flags`` rather than silently dropped or asserted.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models.core import MappingStatus, ReviewStatus, enum_value
from app.models.controls import ObligationControlMapping
from app.models.evidence import ControlTest, Evidence, EvidenceControlLink
from app.models.obligations import ImpactAssessment, Obligation, RegulatoryChange
from app.models.regulatory import RegulationVersion
from app.models.remediation import ComplianceGap, RemediationAction

log = get_logger("regulens.impact")

SIMILARITY_FLOOR = 0.45


@dataclass
class ImpactResult:
    impact: ImpactAssessment
    affected_obligations: list[str]
    affected_controls: list[str]
    affected_gaps: list[str]
    affected_actions: list[str]
    uncertain_relations: list[str]
    propagation: list[dict[str, Any]]


def _norm(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _sentences(text: str) -> list[str]:
    import re

    parts = re.split(r"(?<=[.!?])\s+", text or "")
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


def _keyword_overlap(a: str, b: str) -> float:
    """Overlap coefficient over distinctive (long) terms."""
    import re

    def terms(t: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 4}

    ta, tb = terms(a), terms(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _affected_obligations(session: Session, change: RegulatoryChange) -> tuple[list[Obligation], list[str]]:
    obligations = list(
        session.scalars(
            select(Obligation).where(
                Obligation.regulation_id == change.regulation_id,
                Obligation.review_status == ReviewStatus.APPROVED,
            )
        )
    )
    changed_text = " ".join(filter(None, [change.added_text, change.removed_text, change.summary]))
    change_sentences = _sentences(changed_text)
    matched: list[Obligation] = []
    uncertain: list[str] = []
    for obl in obligations:
        obl_text = f"{obl.obligation_text} {obl.quoted_text}"
        clause_hit = False
        if change.clause_ref and obl.source_section:
            if change.clause_ref.lower() in (obl.source_section or "").lower():
                clause_hit = True
        # full-text similarity understates relatedness when the change and the
        # obligation quote different spans of the same provision, so take the
        # best of: full similarity, best sentence-pair similarity, keyword overlap
        full = _similarity(changed_text, obl_text)
        best_sent = 0.0
        for cs in change_sentences[:12]:
            for os_ in _sentences(obl_text)[:8]:
                s = _similarity(cs, os_)
                if s > best_sent:
                    best_sent = s
        overlap = _keyword_overlap(changed_text, obl_text)
        text_score = max(full, best_sent, overlap)
        basis = "clause reference match" if clause_hit else f"best-signal {text_score:.2f} (full={full:.2f}, sent={best_sent:.2f}, kw={overlap:.2f})"
        if clause_hit or text_score >= SIMILARITY_FLOOR:
            matched.append(obl)
            if not clause_hit and text_score < 0.72:
                uncertain.append(
                    f"Obligation {obl.obligation_uid} linked by text similarity ({text_score:.2f}) only — "
                    "no clause-number match. Requires human confirmation."
                )
        elif text_score >= 0.35:
            uncertain.append(
                f"Obligation {obl.obligation_uid} scored {text_score:.2f} against the change text — below the "
                f"{SIMILARITY_FLOOR:.2f} floor, so it was NOT linked. Review if you believe it is affected."
            )
    return matched, uncertain


def analyze_impact(
    session: Session,
    change_id: str,
    *,
    actor: str = "impact_agent",
    recalculate_risk: bool = True,
) -> ImpactResult:
    from app.services.audit import record_audit

    change = session.get(RegulatoryChange, change_id)
    if change is None:
        raise ValueError(f"Unknown regulatory change {change_id}")

    propagation: list[dict[str, Any]] = [
        {"step": 1, "action": "load change", "target": change.change_uid, "detail": enum_value(change.change_type)}
    ]

    obligations, uncertain = _affected_obligations(session, change)
    propagation.append(
        {
            "step": 2,
            "action": "match obligations",
            "target": [o.obligation_uid for o in obligations],
            "detail": "clause-reference match or text similarity >= floor",
        }
    )

    obligation_ids = [o.id for o in obligations]
    mappings: list[ObligationControlMapping] = []
    if obligation_ids:
        mappings = [
            m
            for m in session.scalars(
                select(ObligationControlMapping).where(ObligationControlMapping.obligation_id.in_(obligation_ids))
            )
            if m.status == MappingStatus.APPROVED
        ]
    propagation.append(
        {
            "step": 3,
            "action": "resolve approved mappings",
            "target": [m.id for m in mappings],
            "detail": "candidate mappings are deliberately excluded from impact propagation",
        }
    )
    for m in mappings:
        if m.status != MappingStatus.APPROVED:
            uncertain.append(f"Mapping {m.id} is not approved; its effect is not asserted.")

    control_ids = sorted({m.control_id for m in mappings})

    evidence_ids: list[str] = []
    if control_ids:
        evidence_ids = [
            l.evidence_id
            for l in session.scalars(
                select(EvidenceControlLink).where(EvidenceControlLink.control_id.in_(control_ids))
            )
        ]
    test_ids = (
        [t.id for t in session.scalars(select(ControlTest).where(ControlTest.control_id.in_(control_ids)))]
        if control_ids
        else []
    )
    propagation.append(
        {
            "step": 4,
            "action": "collect evidence and tests",
            "target": {"evidence": evidence_ids, "tests": test_ids},
            "detail": "dependency-aware walk from controls",
        }
    )

    gaps: list[ComplianceGap] = []
    if obligation_ids or control_ids:
        stmt = select(ComplianceGap).where(
            (ComplianceGap.obligation_id.in_(obligation_ids)) | (ComplianceGap.control_id.in_(control_ids))
        )
        gaps = [g for g in session.scalars(stmt) if g.status in ("open", "in_remediation")]
    gap_ids = [g.id for g in gaps]

    actions = (
        [
            a
            for a in session.scalars(select(RemediationAction).where(RemediationAction.gap_id.in_(gap_ids)))
            if a.status in ("proposed", "approved", "in_progress")
        ]
        if gap_ids
        else []
    )
    propagation.append(
        {
            "step": 5,
            "action": "resolve gaps and remediation",
            "target": {"gaps": gap_ids, "actions": [a.action_uid for a in actions]},
            "detail": "open gaps only",
        }
    )

    impact_score = _impact_score(change, obligations, mappings, gaps, actions)
    priority_delta = _priority_delta(change, gaps)

    narrative = (
        f"Change {change.change_uid} ({enum_value(change.change_type)}) was analysed. "
        f"{len(obligations)} approved obligation(s), {len(mappings)} approved mapping(s), "
        f"{len(control_ids)} control(s), {len(evidence_ids)} evidence artefact(s), {len(test_ids)} test(s), "
        f"{len(gaps)} open gap(s) and {len(actions)} active remediation action(s) are within the impact radius. "
        f"{change.summary}"
    )

    impact = ImpactAssessment(
        impact_uid=_uid(session),
        change_id=change.id,
        affected_obligation_ids=json.dumps(obligation_ids),
        affected_control_ids=json.dumps(control_ids),
        affected_gap_ids=json.dumps(gap_ids),
        affected_action_ids=json.dumps([a.id for a in actions]),
        affected_mapping_ids=json.dumps([m.id for m in mappings]),
        impact_score=impact_score,
        priority_delta=priority_delta,
        narrative=narrative,
        uncertainty_flags=json.dumps(uncertain),
        propagation_path=json.dumps(propagation),
        status="draft",
        created_by=actor,
    )
    session.add(impact)
    session.flush()

    record_audit(
        session,
        actor=actor,
        action="impact.analyzed",
        entity_type="regulatory_change",
        entity_id=change.id,
        entity_uid=change.change_uid,
        after={
            "impact_uid": impact.impact_uid,
            "score": impact_score,
            "obligations": len(obligation_ids),
            "controls": len(control_ids),
            "gaps": len(gap_ids),
            "uncertain": len(uncertain),
        },
    )

    if recalculate_risk and gaps:
        from app.services.risk import score_gap

        for gap in gaps:
            score_gap(session, gap, actor="impact_agent")

    log.info(
        "impact.analyzed",
        change=change.change_uid,
        obligations=len(obligations),
        controls=len(control_ids),
        gaps=len(gaps),
        uncertain=len(uncertain),
    )

    return ImpactResult(
        impact=impact,
        affected_obligations=obligation_ids,
        affected_controls=control_ids,
        affected_gaps=gap_ids,
        affected_actions=[a.id for a in actions],
        uncertain_relations=uncertain,
        propagation=propagation,
    )


def _impact_score(
    change: RegulatoryChange,
    obligations: list[Obligation],
    mappings: list[ObligationControlMapping],
    gaps: list[ComplianceGap],
    actions: list[RemediationAction],
) -> float:
    """A transparent, bounded impact score. Documented as a heuristic ordering
    aid, not a probability."""
    base = {
        "added": 60.0,
        "removed": 35.0,
        "modified": 45.0,
        "deadline_change": 65.0,
        "applicability_change": 55.0,
        "threshold_change": 50.0,
        "requires_human_interpretation": 40.0,
    }.get(enum_value(change.change_type), 40.0)
    score = base
    score += min(20.0, len(obligations) * 4.0)
    score += min(15.0, len(gaps) * 3.0)
    score += min(10.0, len(actions) * 2.0)
    if change.confidence < 0.6:
        score -= 5.0
    return round(min(100.0, max(0.0, score)), 2)


def _priority_delta(change: RegulatoryChange, gaps: list[ComplianceGap]) -> float:
    if enum_value(change.change_type) in ("added", "deadline_change", "applicability_change"):
        return round(min(25.0, 5.0 + len(gaps) * 2.0), 2)
    return 0.0


def _uid(session: Session) -> str:
    from datetime import datetime
    import secrets

    for _ in range(10):
        candidate = f"IMP-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
        if not session.scalar(
            select(ImpactAssessment.id).where(ImpactAssessment.impact_uid == candidate).limit(1)
        ):
            return candidate
    return f"IMP-{secrets.token_hex(8).upper()}"  # pragma: no cover


__all__ = ["analyze_impact", "ImpactResult"]

