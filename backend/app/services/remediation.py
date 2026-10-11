"""Remediation recommendation engine.

Recommendations are generated from the gap type using explicit templates.
Estimated cost and duration are only populated when a value is genuinely
known; otherwise they stay ``None`` and the record carries an explicit
``uncertainty_notes`` entry. Closing a gap requires a human approval and
closure evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging_config import get_logger
from app.models.core import GapType, RemediationStatus, ReviewStatus, enum_value
from app.models.remediation import ComplianceGap, RemediationAction, RemediationDependency

log = get_logger("regulens.remediation")


@dataclass
class RemediationTemplate:
    action: str
    reason: str
    owner_role: str
    verification: str
    closure_evidence: str
    expected_risk_reduction: float
    duration_days: int | None
    cost_inr: float | None
    uncertainty_notes: str | None
    required_capability: str


TEMPLATES: dict[str, RemediationTemplate] = {
    GapType.NO_CONTROL_MAPPED.value: RemediationTemplate(
        action="Design and implement a new internal control that addresses this obligation, or formally "
               "document and justify an existing compensating control.",
        reason="No control is mapped to the obligation, so compliance cannot be demonstrated at all.",
        owner_role="Compliance Control Owner",
        verification="Re-run mapping suggestion, obtain reviewer approval of the new mapping, then execute a "
                     "design test and an operating test over a full control cycle.",
        closure_evidence="Approved control definition, approved obligation-control mapping, design test result, "
                         "and at least one period of approved operating evidence.",
        expected_risk_reduction=0.55,
        duration_days=45,
        cost_inr=None,
        uncertainty_notes="Cost is not estimated because control design effort is organisation-specific.",
        required_capability="control_design",
    ),
    GapType.CONTROL_NOT_APPROVED.value: RemediationTemplate(
        action="Review the candidate mappings and approve or reject them with a recorded rationale.",
        reason="Candidate mappings exist but none is approved, so the obligation remains unverified.",
        owner_role="Compliance Review Lead",
        verification="Reviewer records an approval decision; the mapping status changes to approved or rejected.",
        closure_evidence="Approval record with reviewer identity, timestamp and rationale.",
        expected_risk_reduction=0.35,
        duration_days=7,
        cost_inr=0,
        uncertainty_notes=None,
        required_capability="review",
    ),
    GapType.DESIGN_GAP.value: RemediationTemplate(
        action="Complete the control definition (objective, activity, owner, frequency, preventive/detective "
               "classification, testing procedure and expected evidence) and re-run the design test.",
        reason="The control's design is not fully specified, so design effectiveness cannot be asserted.",
        owner_role="Control Owner",
        verification="Design test returns PASS with all design components present.",
        closure_evidence="Updated control definition and a passing design test.",
        expected_risk_reduction=0.30,
        duration_days=20,
        cost_inr=None,
        uncertainty_notes="No cost estimate: documentation effort varies by control.",
        required_capability="documentation",
    ),
    GapType.MISSING_EVIDENCE.value: RemediationTemplate(
        action="Define the evidence set expected for the control, collect it for the current period and submit "
               "it for review.",
        reason="The control has no linked evidence, so operating effectiveness cannot be demonstrated.",
        owner_role="Control Owner",
        verification="Evidence artefacts are linked to the control, reviewed and marked sufficient for a full period.",
        closure_evidence="Reviewed evidence artefacts covering the current control period.",
        expected_risk_reduction=0.45,
        duration_days=15,
        cost_inr=None,
        uncertainty_notes=None,
        required_capability="evidence_collection",
    ),
    GapType.STALE_EVIDENCE.value: RemediationTemplate(
        action="Re-collect evidence for the current period and review it.",
        reason="The linked evidence predates the freshness window, so it no longer supports the control.",
        owner_role="Control Owner",
        verification="New evidence is linked, fresh, reviewed and sufficient.",
        closure_evidence="Fresh reviewed evidence covering the current period.",
        expected_risk_reduction=0.25,
        duration_days=10,
        cost_inr=None,
        uncertainty_notes=None,
        required_capability="evidence_collection",
    ),
    GapType.INSUFFICIENT_EVIDENCE.value: RemediationTemplate(
        action="Strengthen evidence collection so that the control's operation is demonstrable, or narrow the "
               "control's stated objective to what the evidence can actually support.",
        reason="Evidence is present but insufficient to demonstrate that the control operated effectively.",
        owner_role="Control Owner",
        verification="Operating test returns PASS with sufficient, approved evidence.",
        closure_evidence="Operating test result PASS plus approved sufficient evidence.",
        expected_risk_reduction=0.35,
        duration_days=20,
        cost_inr=None,
        uncertainty_notes=None,
        required_capability="evidence_collection",
    ),
    GapType.FAILED_CONTROL_TEST.value: RemediationTemplate(
        action="Investigate the failed test, remediate the control deficiency, and re-test.",
        reason="A control test failed, which is evidence that the control did not operate as designed.",
        owner_role="Control Owner",
        verification="Re-test of the remediated control returns PASS without exceptions.",
        closure_evidence="Re-test result PASS plus remediation evidence and reviewer approval.",
        expected_risk_reduction=0.55,
        duration_days=30,
        cost_inr=None,
        uncertainty_notes="Cost depends on the root cause identified by the investigation.",
        required_capability="remediation",
    ),
    GapType.INCOMPLETE_POLICY_COVERAGE.value: RemediationTemplate(
        action="Link the control to an approved policy version, or update the policy to cover the control's scope.",
        reason="The control does not trace to an approved policy version, weakening its authority and audit trail.",
        owner_role="Policy Owner",
        verification="Control is linked to an approved policy version.",
        closure_evidence="Approved policy version linked to the control.",
        expected_risk_reduction=0.15,
        duration_days=10,
        cost_inr=None,
        uncertainty_notes=None,
        required_capability="documentation",
    ),
    GapType.CHANGE_NOT_ASSESSED.value: RemediationTemplate(
        action="Run the regulatory impact analysis for the change and record the impact assessment.",
        reason="A regulatory change has not been assessed, so downstream effects are unknown.",
        owner_role="Regulatory Intelligence Lead",
        verification="Impact assessment created, reviewed and linked to the change.",
        closure_evidence="Reviewed impact assessment with affected obligations, controls and gaps.",
        expected_risk_reduction=0.30,
        duration_days=10,
        cost_inr=None,
        uncertainty_notes=None,
        required_capability="regulatory_analysis",
    ),
    GapType.REMEDIATION_OVERDUE.value: RemediationTemplate(
        action="Escalate the overdue remediation action and agree a revised completion date with the owner.",
        reason="A mandatory remediation deadline has already passed.",
        owner_role="Compliance Head",
        verification="Revised action approved with a deadline in the future, or the gap is closed with evidence.",
        closure_evidence="Approved revised plan or closure evidence.",
        expected_risk_reduction=0.20,
        duration_days=5,
        cost_inr=None,
        uncertainty_notes=None,
        required_capability="escalation",
    ),
}

DEFAULT_TEMPLATE = RemediationTemplate(
    action="Define a remediation plan for this gap and obtain approval before execution.",
    reason="A gap of a type without a specific template was detected.",
    owner_role="Compliance Officer",
    verification="Plan approved; closure evidence provided and reviewed.",
    closure_evidence="Approved plan plus closure evidence.",
    expected_risk_reduction=0.25,
    duration_days=20,
    cost_inr=None,
    uncertainty_notes="Generic template: action, cost and duration require owner confirmation.",
    required_capability="remediation",
)


def generate_for_gap(
    session: Session,
    gap: ComplianceGap,
    *,
    due_within_days: int = 60,
    actor: str = "remediation_agent",
) -> RemediationAction:
    existing = session.scalar(
        select(RemediationAction).where(
            RemediationAction.gap_id == gap.id,
            RemediationAction.status.in_([RemediationStatus.PROPOSED, RemediationStatus.APPROVED, RemediationStatus.IN_PROGRESS]),
        ).limit(1)
    )
    if existing:
        return existing

    template = TEMPLATES.get(enum_value(gap.gap_type), DEFAULT_TEMPLATE)
    risk = _latest_risk(session, gap.id)
    reduction = template.expected_risk_reduction
    if risk is not None:
        # scale the expected reduction by the current risk band
        multiplier = {"critical": 1.0, "high": 0.9, "medium": 0.8, "low": 0.6}.get(risk.score_band, 0.8)
        reduction = round(min(0.95, reduction * multiplier), 3)

    deadline = _resolve_deadline(session, gap, due_within_days)
    action = RemediationAction(
        action_uid=_uid(session),
        gap_id=gap.id,
        proposed_action=template.action,
        reason=f"{template.reason} Gap evidence: {gap.detection_basis}",
        expected_risk_reduction=reduction,
        estimated_cost_inr=template.cost_inr,
        cost_is_estimate=template.cost_inr is not None,
        estimated_duration_days=template.duration_days,
        duration_is_estimate=template.duration_days is not None,
        suggested_owner_role=template.owner_role,
        dependencies=json.dumps([]),
        deadline=deadline,
        verification_procedure=template.verification,
        required_evidence_for_closure=template.closure_evidence,
        approval_required=True,
        status=RemediationStatus.PROPOSED,
        uncertainty_notes=template.uncertainty_notes,
        generated_by=actor,
        required_capability=template.required_capability,
    )
    session.add(action)
    session.flush()
    log.info("remediation.generated", gap=gap.gap_uid, action=action.action_uid)
    return action


def generate_for_gaps(session: Session, gap_ids: list[str] | None = None) -> list[RemediationAction]:
    stmt = select(ComplianceGap).where(ComplianceGap.status.in_(["open", "in_remediation"]))
    if gap_ids:
        stmt = stmt.where(ComplianceGap.id.in_(gap_ids))
    created: list[RemediationAction] = []
    for gap in session.scalars(stmt):
        created.append(generate_for_gap(session, gap))
    return created


def set_dependencies(session: Session, action_id: str, depends_on: list[str]) -> list[RemediationDependency]:
    session.query(RemediationDependency).filter(RemediationDependency.action_id == action_id).delete()
    created: list[RemediationDependency] = []
    for dep_id in depends_on:
        if dep_id == action_id:
            continue
        dep = RemediationDependency(action_id=action_id, depends_on_action_id=dep_id)
        session.add(dep)
        created.append(dep)
    action = session.get(RemediationAction, action_id)
    if action is not None:
        action.dependencies = json.dumps([d for d in depends_on if d != action_id])
    session.flush()
    return created


def _latest_risk(session: Session, gap_id: str):
    from app.models.remediation import RiskAssessment

    return session.scalar(
        select(RiskAssessment).where(RiskAssessment.gap_id == gap_id).order_by(RiskAssessment.created_at.desc()).limit(1)
    )


def _resolve_deadline(session: Session, gap: ComplianceGap, due_within_days: int) -> date | None:
    from app.models.remediation import RiskAssessment

    risk = _latest_risk(session, gap.id)
    if risk and risk.hard_deadline:
        return risk.hard_deadline
    if gap.obligation and gap.obligation.deadline_date:
        return gap.obligation.deadline_date
    return date.today() + timedelta(days=due_within_days)


def _uid(session: Session) -> str:
    from datetime import datetime
    import secrets

    for _ in range(10):
        candidate = f"REM-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
        if not session.scalar(
            select(RemediationAction.id).where(RemediationAction.action_uid == candidate).limit(1)
        ):
            return candidate
    return f"REM-{secrets.token_hex(8).upper()}"  # pragma: no cover


__all__ = [
    "generate_for_gap",
    "generate_for_gaps",
    "set_dependencies",
    "TEMPLATES",
    "RemediationTemplate",
]
