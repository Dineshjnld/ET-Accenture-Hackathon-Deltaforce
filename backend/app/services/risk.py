"""Transparent, configurable risk scoring.

    risk_score = Σ (weight_i × normalised_factor_i)  scaled to 0..100

The default weights are an **illustrative engineering default chosen for
transparency, not a regulatory or calibrated probability model**. Every score
is stored together with the per-factor contributions so the UI can explain
exactly how the number was produced. ``calibrated`` stays ``False`` unless the
weights have been empirically calibrated against observed outcomes.

Mandatory regulatory deadlines are handled by a **hard constraint** that is
independent of the weighted score: a breach forces the highest band regardless
of the arithmetic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging_config import get_logger
from app.models.core import RemediationStatus, ReviewStatus, enum_value
from app.models.remediation import ComplianceGap, RemediationAction, RiskAssessment
from app.services.evidence import evidence_state_for_control

log = get_logger("regulens.risk")

DEFAULT_WEIGHTS: dict[str, float] = {
    "regulatory_severity": 0.35,
    "failure_likelihood": 0.25,
    "exposure": 0.20,
    "urgency": 0.20,
}

BAND_HIGH = "high"
BAND_MEDIUM = "medium"
BAND_LOW = "low"
BAND_CRITICAL = "critical"


@dataclass
class RiskExplanation:
    factors: dict[str, float]
    weights: dict[str, float]
    contributions: dict[str, float]
    raw_score: float
    score: float
    band: str
    hard_constraint: str | None
    narrative: str
    caveats: list[str] = field(default_factory=list)


def normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(abs(v) for v in weights.values())
    if total <= 0:
        raise ValueError("Risk weights must sum to a positive value")
    return {k: round(v / total, 6) for k, v in weights.items()}


def _severity_from_text(value: str) -> float:
    return {"critical": 100.0, "high": 80.0, "medium": 50.0, "low": 25.0, "unknown": 50.0}.get(
        (value or "unknown").lower(), 50.0
    )


def compute_factor_scores(
    session: Session,
    gap: ComplianceGap,
    *,
    as_of: date | None = None,
) -> dict[str, float]:
    """Derive the normalised (0..100) factor inputs from stored facts only."""
    from app.services.assessment import assess_operating

    as_of = as_of or date.today()
    obligation = gap.obligation

    # 1. Regulatory severity — from the obligation's stated modal force.
    if obligation is not None:
        severity = _severity_from_text(enum_value(obligation.severity))
        if obligation.severity_rationale and "binding modal" in obligation.severity_rationale.lower():
            severity = max(severity, 70.0)
        if obligation.deadline_date or obligation.deadline_text:
            severity = min(100.0, severity + 10.0)
    else:
        severity = 50.0

    # 2. Likelihood of failure — observed test/evidence state, not assumption.
    likelihood = 50.0
    if gap.control_id:
        op = assess_operating(session, gap.control)
        state = enum_value(op.status)
        likelihood = {
            "ineffective": 90.0,
            "insufficient_evidence": 70.0,
            "not_tested": 60.0,
            "partially_effective": 50.0,
            "effective": 15.0,
        }.get(state, 50.0)
    elif gap.gap_type == "obligation_without_control":
        likelihood = 85.0
    elif gap.gap_type == "regulatory_change_not_assessed":
        likelihood = 65.0

    # 3. Exposure — how much of the obligation is uncovered.
    exposure = 50.0
    if gap.gap_type == "obligation_without_control":
        exposure = 90.0
    elif gap.gap_type == "failed_control_test":
        exposure = 80.0
    elif gap.gap_type == "missing_evidence":
        exposure = 65.0
    elif gap.gap_type == "stale_evidence":
        exposure = 55.0
    elif gap.gap_type == "incomplete_policy_coverage":
        exposure = 35.0
    evidence_state = evidence_state_for_control(session, gap.control_id) if gap.control_id else None
    if evidence_state and evidence_state.get("insufficient", 0) > 0:        exposure = min(100.0, exposure + 10.0)

    # 4. Urgency — days to the nearest mandatory deadline.
    urgency = 20.0
    deadlines: list[date] = []
    for action in session.scalars(
        select(RemediationAction).where(RemediationAction.gap_id == gap.id, RemediationAction.deadline.is_not(None))
    ):
        deadlines.append(action.deadline)
    if obligation and obligation.deadline_date:
        deadlines.append(obligation.deadline_date)
    if deadlines:
        nearest = min(deadlines)
        days = (nearest - as_of).days
        if days < 0:
            urgency = 100.0
        elif days <= 7:
            urgency = 90.0
        elif days <= 30:
            urgency = 75.0
        elif days <= 90:
            urgency = 55.0
        elif days <= 365:
            urgency = 35.0
        else:
            urgency = 20.0

    # 5. Evidence confidence — used as a documented adjustment, not a weight by default.
    evidence_conf = 50.0
    if evidence_state:
        coverage = evidence_state.get("coverage", 0.0)
        evidence_conf = round(coverage * 100.0, 2)

    # 6. Existing mitigation — approved, in-flight remediation.
    mitigation = 0.0
    for action in session.scalars(
        select(RemediationAction).where(
            RemediationAction.gap_id == gap.id,
            RemediationAction.status.in_([RemediationStatus.APPROVED, RemediationStatus.IN_PROGRESS]),
        )
    ):
        mitigation = max(mitigation, min(100.0, (action.expected_risk_reduction or 0.0) * 100.0))

    return {
        "regulatory_severity": round(severity, 2),
        "failure_likelihood": round(likelihood, 2),
        "exposure": round(exposure, 2),
        "urgency": round(urgency, 2),
        "evidence_confidence": round(evidence_conf, 2),
        "existing_mitigation": round(mitigation, 2),
    }


def score_gap(
    session: Session,
    gap: ComplianceGap,
    *,
    weights: dict[str, float] | None = None,
    as_of: date | None = None,
    actor: str = "risk_agent",
) -> tuple[RiskAssessment, RiskExplanation]:
    weights = normalise_weights(weights or settings.RISK_WEIGHTS or DEFAULT_WEIGHTS)
    factors = compute_factor_scores(session, gap, as_of=as_of)

    weighted_keys = ["regulatory_severity", "failure_likelihood", "exposure", "urgency"]
    contributions = {k: round(weights.get(k, 0.0) * factors.get(k, 0.0), 3) for k in weighted_keys}
    raw = sum(contributions.values())

    # documented, optional mitigations: evidence confidence and existing mitigation
    evidence_conf = factors.get("evidence_confidence", 50.0)
    mitigation = factors.get("existing_mitigation", 0.0)
    # evidence confidence is an *uncertainty* signal: low confidence widens risk, it does not reduce it
    uncertainty_penalty = round((50.0 - evidence_conf) * 0.10, 3)
    raw_adjusted = max(0.0, raw + uncertainty_penalty - mitigation * 0.15)
    score = round(min(100.0, max(0.0, raw_adjusted)), 2)

    hard_deadline, days_to_deadline, breached, hard_flag = _hard_constraint(session, gap, as_of=as_of)
    if hard_flag:
        band = BAND_CRITICAL
    elif score >= settings.HIGH_RISK_THRESHOLD:
        band = BAND_HIGH
    elif score >= settings.MEDIUM_RISK_THRESHOLD:
        band = BAND_MEDIUM
    else:
        band = BAND_LOW

    explanation = RiskExplanation(
        factors=factors,
        weights=weights,
        contributions=contributions,
        raw_score=round(raw, 3),
        score=score,
        band=band,
        hard_constraint=hard_flag,
        narrative=_narrative(factors, contributions, score, band, hard_flag),
        caveats=[
            "Scores are deterministic and explainable but NOT empirically calibrated probabilities.",
            "Factor values are derived from stored facts; 'unknown' inputs default to a neutral 50 and are "
            "flagged in the factor panel.",
        ],
    )

    assessment = RiskAssessment(
        gap_id=gap.id,
        regulatory_severity=factors["regulatory_severity"],
        failure_likelihood=factors["failure_likelihood"],
        exposure=factors["exposure"],
        urgency=factors["urgency"],
        evidence_confidence=evidence_conf,
        existing_mitigation=mitigation,
        weights=json.dumps(weights),
        score=score,
        score_band=band,
        hard_deadline=hard_deadline,
        days_to_deadline=days_to_deadline,
        deadline_breached=breached,
        hard_constraint_flag=hard_flag,
        explanation=json.dumps(
            {
                "factors": factors,
                "weights": weights,
                "contributions": contributions,
                "raw_score": explanation.raw_score,
                "uncertainty_penalty": uncertainty_penalty,
                "mitigation_relief": round(mitigation * 0.15, 3),
                "narrative": explanation.narrative,
                "caveats": explanation.caveats,
                "formula": "score = Σ(weight_i × factor_i) + uncertainty_penalty − mitigation_relief, "
                "bounded to 0..100; hard deadline breaches force the CRITICAL band.",
            }
        ),
        calibrated=False,
        calculated_by=actor,
    )
    session.add(assessment)
    session.flush()
    return assessment, explanation


def _hard_constraint(
    session: Session, gap: ComplianceGap, *, as_of: date | None = None
) -> tuple[date | None, int | None, bool, str | None]:
    as_of = as_of or date.today()
    deadlines = [
        a.deadline
        for a in session.scalars(
            select(RemediationAction).where(
                RemediationAction.gap_id == gap.id, RemediationAction.deadline.is_not(None)
            )
        )
    ]
    if gap.obligation and gap.obligation.deadline_date:
        deadlines.append(gap.obligation.deadline_date)
    if not deadlines:
        return None, None, False, None
    nearest = min(deadlines)
    days = (nearest - as_of).days
    if days < 0:
        return nearest, days, True, "MANDATORY_DEADLINE_BREACHED"
    if days <= 30:
        return nearest, days, False, "MANDATORY_DEADLINE_IMMINENT"
    return nearest, days, False, None


def _narrative(
    factors: dict[str, float],
    contributions: dict[str, float],
    score: float,
    band: str,
    hard: str | None,
) -> str:
    parts = [
        f"Risk score {score:.1f}/100 (band: {band}).",
        "Contributors: " + ", ".join(f"{k} {v:.2f}" for k, v in contributions.items() if v) + ".",
        f"Severity from the obligation's stated modal force: {factors['regulatory_severity']:.0f}/100.",
        f"Likelihood of failure inferred from observed test/evidence state: {factors['failure_likelihood']:.0f}/100.",
        f"Exposure from the gap type: {factors['exposure']:.0f}/100.",
        f"Urgency from the nearest mandatory deadline: {factors['urgency']:.0f}/100.",
    ]
    if hard == "MANDATORY_DEADLINE_BREACHED":
        parts.append("A mandatory regulatory deadline has already been breached, so the finding is escalated to "
                     "CRITICAL independently of the weighted arithmetic.")
    elif hard == "MANDATORY_DEADLINE_IMMINENT":
        parts.append("A mandatory regulatory deadline falls within 30 days.")
    return " ".join(parts)


def recalculate_all(session: Session, *, actor: str = "risk_agent") -> list[RiskAssessment]:
    created: list[RiskAssessment] = []
    from app.services.audit import record_audit

    for gap in session.scalars(select(ComplianceGap).where(ComplianceGap.status.in_(["open", "in_remediation"]))).all():
        assessment, _ = score_gap(session, gap, actor=actor)
        created.append(assessment)
    record_audit(
        session,
        actor=actor,
        action="risk.recalculated",
        entity_type="risk_assessment",
        after={"assessments": len(created)},
    )
    return created


def risk_summary(session: Session) -> dict[str, Any]:
    rows = list(
        session.execute(
            select(RiskAssessment.score_band, RiskAssessment.score).order_by(RiskAssessment.created_at.desc())
        ).all()
    )
    latest: dict[str, float] = {}
    for row in session.execute(
        select(ComplianceGap.id, RiskAssessment.score, RiskAssessment.score_band)
        .join(RiskAssessment, RiskAssessment.gap_id == ComplianceGap.id)
        .order_by(RiskAssessment.created_at.desc())
    ):
        latest.setdefault(row[0], (row[1], row[2]))
    bands: dict[str, int] = {}
    scores: list[float] = []
    for score, band in latest.values():
        bands[band] = bands.get(band, 0) + 1
        scores.append(score)
    return {
        "total_assessed": len(latest),
        "bands": bands,
        "max_score": round(max(scores), 2) if scores else None,
        "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
        "rows_scanned": len(rows),
    }


__all__ = ["score_gap", "compute_factor_scores", "recalculate_all", "risk_summary", "DEFAULT_WEIGHTS", "normalise_weights"]
