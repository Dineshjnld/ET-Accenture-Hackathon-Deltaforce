"""Gap detection.

Each rule produces a :class:`ComplianceGap` with an explicit ``detection_basis``
explanation and a ``truth_status`` that distinguishes a *demonstrated control
failure* (``determinate``) from an *inability to determine* compliance
(``indeterminate``). Gaps never close without an approved remediation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging_config import get_logger
from app.models.core import (
    GapStatus,
    GapType,
    MappingStatus,
    RemediationStatus,
    ReviewStatus,
    TestResult,
    enum_value,
)
from app.models.controls import Control, ObligationControlMapping
from app.models.evidence import ControlTest, Evidence, EvidenceControlLink
from app.models.obligations import Obligation, RegulatoryChange
from app.models.remediation import ComplianceGap, RemediationAction

log = get_logger("regulens.gaps")


@dataclass
class GapFinding:
    gap_type: GapType
    title: str
    description: str
    detection_basis: str
    obligation_id: str | None
    control_id: str | None
    truth_status: str
    severity_hint: str
    context: dict[str, Any] = field(default_factory=dict)
    business_unit_id: str | None = None


def _existing(session: Session, gap_type: GapType, obligation_id: str | None, control_id: str | None) -> bool:
    stmt = select(ComplianceGap).where(
        ComplianceGap.gap_type == gap_type,
        ComplianceGap.status.in_([GapStatus.OPEN, GapStatus.IN_REMEDIATION]),
    )
    stmt = stmt.where(ComplianceGap.obligation_id == obligation_id) if obligation_id else stmt.where(
        ComplianceGap.obligation_id.is_(None)
    )
    if control_id:
        stmt = stmt.where(ComplianceGap.control_id == control_id)
    return session.scalar(stmt.limit(1)) is not None


def detect_gaps(session: Session, *, obligation_ids: list[str] | None = None) -> list[ComplianceGap]:
    """Run the full gap rule set. Returns newly created gaps."""
    from app.services.assessment import assess_design, assess_operating
    from app.services.audit import record_audit

    findings: list[GapFinding] = []

    obl_stmt = select(Obligation).where(Obligation.review_status == ReviewStatus.APPROVED)
    if obligation_ids:
        obl_stmt = obl_stmt.where(Obligation.id.in_(obligation_ids))
    obligations = list(session.scalars(obl_stmt))

    for obl in obligations:
        approved = [
            m
            for m in session.scalars(
                select(ObligationControlMapping).where(ObligationControlMapping.obligation_id == obl.id)
            )
            if m.status == MappingStatus.APPROVED
        ]
        candidates = [
            m
            for m in session.scalars(
                select(ObligationControlMapping).where(ObligationControlMapping.obligation_id == obl.id)
            )
            if m.status == MappingStatus.CANDIDATE
        ]
        if not approved and not candidates:
            findings.append(
                GapFinding(
                    gap_type=GapType.NO_CONTROL_MAPPED,
                    title=f"No control addresses obligation {obl.obligation_uid}",
                    description=(
                        "The approved obligation has neither an approved nor a candidate control mapping. "
                        "Compliance cannot be demonstrated."
                    ),
                    detection_basis=(
                        f"Rule: approved obligation without any mapping. Obligation {obl.obligation_uid} "
                        f"(source: document {obl.source_document_id}, page {obl.source_page}, "
                        f"section {obl.source_section})."
                    ),
                    obligation_id=obl.id,
                    control_id=None,
                    truth_status="determinate",
                    severity_hint=enum_value(obl.severity),
                    context={"quoted_text": obl.quoted_text[:500]},
                )
            )
        elif not approved and candidates:
            findings.append(
                GapFinding(
                    gap_type=GapType.CONTROL_NOT_APPROVED,
                    title=f"Control mapping for {obl.obligation_uid} is not approved",
                    description=(
                        f"{len(candidates)} candidate mapping(s) exist but none has been approved by a reviewer. "
                        "Until approval, the obligation is unverified."
                    ),
                    detection_basis=f"Rule: obligation has candidate mappings but zero approved mappings ({len(candidates)} candidates).",
                    obligation_id=obl.id,
                    control_id=candidates[0].control_id if candidates else None,
                    truth_status="indeterminate",
                    severity_hint="medium",
                    context={"candidate_mapping_ids": [m.id for m in candidates]},
                    business_unit_id=candidates[0].control.business_unit_id if candidates else None,
                )
            )
        else:
            for mapping in approved:
                control = mapping.control
                if control is None:
                    continue
                design = assess_design(control)
                operating = session.get(Control, control.id)
                if design.status.value in ("partially_addressed", "unable_to_determine"):
                    findings.append(
                        GapFinding(
                            gap_type=GapType.DESIGN_GAP,
                            title=f"Control {control.control_uid} design is incomplete",
                            description=f"Design assessment: {design.status.value}. {design.rationale}",
                            detection_basis=f"Design test components missing: {', '.join(design.missing) or 'none'}",
                            obligation_id=obl.id,
                            control_id=control.id,
                            truth_status="indeterminate",
                            severity_hint="medium" if design.completeness >= 0.6 else "high",
                            context={"completeness": design.completeness, "missing": design.missing},
                            business_unit_id=control.business_unit_id,
                        )
                    )
                elif design.status.value == "not_addressed":
                    findings.append(
                        GapFinding(
                            gap_type=GapType.DESIGN_GAP,
                            title=f"Control {control.control_uid} is not designed to address the obligation",
                            description=design.rationale,
                            detection_basis="Design test: no documented objective, activity, owner or frequency.",
                            obligation_id=obl.id,
                            control_id=control.id,
                            truth_status="determinate",
                            severity_hint="high",
                            context={"completeness": 0.0},
                            business_unit_id=control.business_unit_id,
                        )
                    )

                op = assess_operating(session, control)
                if op.status.value == "not_tested":
                    findings.append(
                        GapFinding(
                            gap_type=GapType.FAILED_CONTROL_TEST,
                            title=f"Control {control.control_uid} has never been tested",
                            description="Operating effectiveness cannot be asserted because no test has been performed.",
                            detection_basis="Rule: approved mapping to a control with zero control tests.",
                            obligation_id=obl.id,
                            control_id=control.id,
                            truth_status="indeterminate",
                            severity_hint="medium",
                            context={"tests": 0},
                            business_unit_id=control.business_unit_id,
                        )
                    )
                elif op.status.value == "ineffective":
                    findings.append(
                        GapFinding(
                            gap_type=GapType.FAILED_CONTROL_TEST,
                            title=f"Control {control.control_uid} failed testing",
                            description=op.rationale,
                            detection_basis="Rule: control test result FAIL.",
                            obligation_id=obl.id,
                            control_id=control.id,
                            truth_status="determinate",
                            severity_hint="high",
                            context={"failed_tests": op.failed_tests},
                            business_unit_id=control.business_unit_id,
                        )
                    )
                elif op.status.value == "insufficient_evidence":
                    findings.append(
                        GapFinding(
                            gap_type=GapType.INSUFFICIENT_EVIDENCE,
                            title=f"Insufficient evidence for control {control.control_uid}",
                            description=op.rationale,
                            detection_basis="Rule: no sufficient, approved, fresh evidence linked to the control.",
                            obligation_id=obl.id,
                            control_id=control.id,
                            truth_status="indeterminate",
                            severity_hint="medium",
                            context={
                                "evidence_count": op.evidence_count,
                                "sufficient": op.sufficient_evidence,
                                "stale": op.stale_evidence,
                            },
                            business_unit_id=control.business_unit_id,
                        )
                    )

                links = list(session.scalars(select(EvidenceControlLink).where(EvidenceControlLink.control_id == control.id)))
                if not links:
                    findings.append(
                        GapFinding(
                            gap_type=GapType.MISSING_EVIDENCE,
                            title=f"No evidence linked to control {control.control_uid}",
                            description="The control is mapped to an approved obligation but has no linked evidence.",
                            detection_basis="Rule: zero evidence_control_links for a control supporting an approved obligation.",
                            obligation_id=obl.id,
                            control_id=control.id,
                            truth_status="indeterminate",
                            severity_hint="medium",
                            context={},
                            business_unit_id=control.business_unit_id,
                        )
                    )
                else:
                    evidence_items = list(
                        session.scalars(select(Evidence).where(Evidence.id.in_([l.evidence_id for l in links])))
                    )
                    from app.services.assessment import assess_evidence

                    for ev in evidence_items:
                        res = assess_evidence(ev)
                        if res.validity.value == "stale":
                            findings.append(
                                GapFinding(
                                    gap_type=GapType.STALE_EVIDENCE,
                                    title=f"Evidence {ev.evidence_uid} for control {control.control_uid} is stale",
                                    description=res.rationale,
                                    detection_basis=(
                                        f"Rule: evidence period ended {res.days_since_period_end} days ago; "
                                        f"freshness window is {settings.EVIDENCE_FRESHNESS_DAYS} days."
                                    ),
                                    obligation_id=obl.id,
                                    control_id=control.id,
                                    truth_status="indeterminate",
                                    severity_hint="medium",
                                    context={"evidence_id": ev.id, "days_since_period_end": res.days_since_period_end},
                                    business_unit_id=control.business_unit_id,
                                )
                            )

    # --- control-level policy coverage and conflicts -----------------------
    for control in session.scalars(select(Control)).all():
        if control.policy_version_id is None:
            findings.append(
                GapFinding(
                    gap_type=GapType.INCOMPLETE_POLICY_COVERAGE,
                    title=f"Control {control.control_uid} is not linked to an approved policy version",
                    description="Controls must trace to a policy version so that the control's authority is documented.",
                    detection_basis="Rule: control.policy_version_id is null.",
                    obligation_id=None,
                    control_id=control.id,
                    truth_status="indeterminate",
                    severity_hint="low",
                    context={},
                    business_unit_id=control.business_unit_id,
                )
            )

    # --- unassessed regulatory changes --------------------------------------
    for change in session.scalars(select(RegulatoryChange).where(RegulatoryChange.review_status == ReviewStatus.PENDING)).all():
        findings.append(
            GapFinding(
                gap_type=GapType.CHANGE_NOT_ASSESSED,
                title=f"Regulatory change {change.change_uid} has not been assessed",
                description=f"{change.change_type}: {change.summary}"[:1000],
                detection_basis="Rule: regulatory_changes row with review_status = pending and no impact assessment.",
                obligation_id=None,
                control_id=None,
                truth_status="indeterminate",
                severity_hint="high" if enum_value(change.change_type) in ("added", "deadline_change") else "medium",
                context={"change_id": change.id, "change_uid": change.change_uid},
            )
        )

    # --- overdue remediation ------------------------------------------------
    today = date.today()
    for action in session.scalars(
        select(RemediationAction).where(
            RemediationAction.status.in_([RemediationStatus.APPROVED, RemediationStatus.IN_PROGRESS])
        )
    ).all():
        if action.deadline and action.deadline < today:
            findings.append(
                GapFinding(
                    gap_type=GapType.REMEDIATION_OVERDUE,
                    title=f"Remediation {action.action_uid} is overdue",
                    description=f"Approved remediation was due on {action.deadline.isoformat()} and is not complete.",
                    detection_basis="Rule: remediation deadline in the past and status not completed/closed.",
                    obligation_id=None,
                    control_id=None,
                    truth_status="determinate",
                    severity_hint="high",
                    context={"action_id": action.id, "deadline": action.deadline.isoformat()},
                )
            )

    # --- deduplicate and persist -------------------------------------------
    created: list[ComplianceGap] = []
    seen: set[tuple] = set()
    for finding in findings:
        key = (finding.gap_type, finding.obligation_id, finding.control_id, finding.title)
        if key in seen:
            continue
        seen.add(key)
        if _existing(session, finding.gap_type, finding.obligation_id, finding.control_id):
            continue
        gap = ComplianceGap(
            gap_uid=_uid("GAP"),
            obligation_id=finding.obligation_id,
            control_id=finding.control_id,
            business_unit_id=finding.business_unit_id,
            gap_type=finding.gap_type,
            title=finding.title,
            description=finding.description,
            detection_basis=finding.detection_basis,
            detection_context=_json(finding.context),
            detected_by="gap_agent",
            status=GapStatus.OPEN,
            severity_hint=finding.severity_hint,
            truth_status=finding.truth_status,
        )
        session.add(gap)
        created.append(gap)
    session.flush()

    if created:
        record_audit(
            session,
            actor="gap_agent",
            action="gaps.detected",
            entity_type="compliance_gap",
            after={
                "created": len(created),
                "by_type": _counts([g.gap_type for g in created]),
                "determinate": sum(1 for g in created if g.truth_status == "determinate"),
                "indeterminate": sum(1 for g in created if g.truth_status == "indeterminate"),
            },
        )
    log.info("gaps.detected", created=len(created))
    return created


def _counts(values: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = str(getattr(v, "value", v))
        out[key] = out.get(key, 0) + 1
    return out


_UID_SEQ = 0


def _uid(prefix: str) -> str:
    from datetime import datetime
    import secrets

    return f"{prefix}-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)[:20000]


__all__ = ["detect_gaps", "GapFinding"]
