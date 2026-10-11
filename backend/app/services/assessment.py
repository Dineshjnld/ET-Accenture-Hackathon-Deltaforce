"""Control testing and evidence assessment.

Design effectiveness and operating effectiveness are assessed **separately** and
only from stated facts:

* Design: derived from the completeness of the control's own definition
  (objective, activity, owner, frequency, testing procedure, expected evidence).
* Operating: derived from evidence and test outcomes only.

A policy document describing a control is *never* treated as evidence that the
control operates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging_config import get_logger
from app.models.core import (
    ControlType,
    DesignEffectiveness,
    EvidenceValidity,
    OperatingEffectiveness,
    ReviewStatus,
    TestResult,
    enum_value,
)
from app.models.controls import Control
from app.models.evidence import ControlTest, Evidence, EvidenceControlLink, TestResult as TestResultModel
from app.models.obligations import Obligation
from app.models.remediation import ComplianceGap

log = get_logger("regulens.assessment")

DESIGN_COMPONENTS = [
    ("control_objective", "control objective"),
    ("control_activity", "control activity"),
    ("owner_role", "control owner"),
    ("frequency", "control frequency"),
    ("testing_procedure", "testing procedure"),
    ("expected_evidence", "expected evidence"),
]


@dataclass
class DesignAssessment:
    status: DesignEffectiveness
    rationale: str
    present: list[str]
    missing: list[str]
    completeness: float


@dataclass
class OperatingAssessment:
    status: OperatingEffectiveness
    rationale: str
    evidence_count: int
    sufficient_evidence: int
    stale_evidence: int
    failed_tests: int
    inconclusive_tests: int
    last_test_at: Any = None


@dataclass
class EvidenceAssessment:
    validity: EvidenceValidity
    rationale: str
    freshness: str
    days_since_period_end: int | None
    review_status: str
    blocking_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Design effectiveness
# ---------------------------------------------------------------------------
def assess_design(control: Control) -> DesignAssessment:
    present: list[str] = []
    missing: list[str] = []
    for attr, label in DESIGN_COMPONENTS:
        value = getattr(control, attr, None)
        if value and str(value).strip():
            present.append(label)
        else:
            missing.append(label)
    completeness = len(present) / len(DESIGN_COMPONENTS)

    if control.control_type == ControlType.UNKNOWN:
        missing.append("preventive/detective classification")
    if control.status == ReviewStatus.REJECTED:
        return DesignAssessment(
            DesignEffectiveness.UNABLE_TO_DETERMINE,
            "Control has been rejected and cannot support compliance.",
            present,
            missing,
            completeness,
        )

    if not missing:
        return DesignAssessment(
            DesignEffectiveness.EFFECTIVE_BY_DESIGN,
            "All design components are documented: " + ", ".join(present) + ".",
            present,
            missing,
            completeness,
        )
    if completeness >= 0.6:
        return DesignAssessment(
            DesignEffectiveness.PARTIALLY_ADDRESSED,
            "Design is partially specified. Missing: " + ", ".join(missing) + ".",
            present,
            missing,
            completeness,
        )
    if not present:
        return DesignAssessment(
            DesignEffectiveness.NOT_ADDRESSED,
            "The control has no documented objective, activity, owner, frequency or testing procedure.",
            present,
            missing,
            completeness,
        )
    return DesignAssessment(
        DesignEffectiveness.UNABLE_TO_DETERMINE,
        "Design information is insufficient to conclude effectiveness. Present: "
        + ", ".join(present)
        + ". Missing: "
        + ", ".join(missing)
        + ".",
        present,
        missing,
        completeness,
    )


# ---------------------------------------------------------------------------
# Evidence assessment
# ---------------------------------------------------------------------------
def assess_evidence(
    evidence: Evidence,
    *,
    as_of: date | None = None,
    freshness_days: int | None = None,
) -> EvidenceAssessment:
    as_of = as_of or date.today()
    freshness_days = freshness_days or settings.EVIDENCE_FRESHNESS_DAYS
    blocking: list[str] = []

    if evidence.validity_status == EvidenceValidity.MISSING:
        return EvidenceAssessment(
            EvidenceValidity.MISSING,
            "No evidence artefact is linked to this control.",
            "unknown",
            None,
            enum_value(evidence.review_status),
            ["no_evidence_artefact"],
        )

    days: int | None = None
    if evidence.evidence_period_end:
        days = (as_of - evidence.evidence_period_end).days
    freshness = "unknown"
    if days is not None:
        freshness = "fresh" if days <= freshness_days else "stale"

    review_status = enum_value(evidence.review_status)
    if freshness == "stale":
        blocking.append(f"evidence period ended {days} days ago (freshness window {freshness_days} days)")
    if evidence.test_result == TestResult.FAIL:
        blocking.append("linked test result is FAIL")
    if evidence.test_result == TestResult.INCONCLUSIVE:
        blocking.append("linked test result is INCONCLUSIVE")

    if freshness == "stale":
        return EvidenceAssessment(EvidenceValidity.STALE, "Evidence is stale. " + "; ".join(blocking) + ".", freshness, days, review_status, blocking)
    if evidence.test_result == TestResult.FAIL:
        return EvidenceAssessment(EvidenceValidity.INSUFFICIENT, "Evidence exists but the associated control test failed.", freshness, days, review_status, blocking)
    if evidence.test_result == TestResult.INCONCLUSIVE:
        return EvidenceAssessment(EvidenceValidity.INCONCLUSIVE, "Evidence exists but testing was inconclusive.", freshness, days, review_status, blocking)
    if review_status == ReviewStatus.PENDING.value:
        return EvidenceAssessment(EvidenceValidity.NOT_REVIEWED, "Evidence has been collected but not yet reviewed.", freshness, days, review_status, ["awaiting_review"])
    if evidence.validity_status == EvidenceValidity.SUFFICIENT and review_status == ReviewStatus.APPROVED.value:
        return EvidenceAssessment(EvidenceValidity.SUFFICIENT, "Evidence is fresh, reviewed and approved as sufficient.", freshness, days, review_status)
    return EvidenceAssessment(EvidenceValidity.INSUFFICIENT, "Evidence is present but does not yet establish that the control operated.", freshness, days, review_status, ["insufficient_basis"])


# ---------------------------------------------------------------------------
# Operating effectiveness
# ---------------------------------------------------------------------------
def assess_operating(session: Session, control: Control, *, as_of: date | None = None) -> OperatingAssessment:
    as_of = as_of or date.today()
    links = list(session.scalars(select(EvidenceControlLink).where(EvidenceControlLink.control_id == control.id)))
    evidence_ids = [l.evidence_id for l in links]
    evidence = (
        list(session.scalars(select(Evidence).where(Evidence.id.in_(evidence_ids)))) if evidence_ids else []
    )
    assessments = [assess_evidence(e, as_of=as_of) for e in evidence]

    tests = list(
        session.scalars(
            select(ControlTest).where(ControlTest.control_id == control.id).order_by(ControlTest.performed_at.desc())
        )
    )
    failed = sum(1 for t in tests if t.result == TestResult.FAIL)
    inconclusive = sum(1 for t in tests if t.result == TestResult.INCONCLUSIVE)
    last_test = tests[0].performed_at if tests else None

    sufficient = sum(1 for a in assessments if a.validity == EvidenceValidity.SUFFICIENT)
    stale = sum(1 for a in assessments if a.validity == EvidenceValidity.STALE)
    missing = sum(1 for a in assessments if a.validity == EvidenceValidity.MISSING)
    not_reviewed = sum(1 for a in assessments if a.validity == EvidenceValidity.NOT_REVIEWED)

    rationale_parts: list[str] = []
    if not tests:
        return OperatingAssessment(
            OperatingEffectiveness.NOT_TESTED,
            "No control test has been performed for this control.",
            len(evidence),
            sufficient,
            stale,
            failed,
            inconclusive,
            last_test,
        )

    if failed:
        rationale_parts.append(f"{failed} test(s) failed")
    if inconclusive:
        rationale_parts.append(f"{inconclusive} test(s) inconclusive")
    if stale:
        rationale_parts.append(f"{stale} evidence artefact(s) stale")
    if missing:
        rationale_parts.append(f"{missing} evidence artefact(s) missing")
    if not_reviewed:
        rationale_parts.append(f"{not_reviewed} evidence artefact(s) awaiting review")
    if sufficient:
        rationale_parts.append(f"{sufficient} evidence artefact(s) sufficient and approved")

    if failed or (evidence and sufficient == 0):
        status = OperatingEffectiveness.INEFFECTIVE
    elif inconclusive or stale or missing:
        status = OperatingEffectiveness.INSUFFICIENT_EVIDENCE
    elif not evidence:
        status = OperatingEffectiveness.INSUFFICIENT_EVIDENCE
    elif sufficient >= 1 and not (inconclusive or stale):
        status = OperatingEffectiveness.EFFECTIVE
    else:
        status = OperatingEffectiveness.PARTIALLY_EFFECTIVE

    return OperatingAssessment(
        status,
        "; ".join(rationale_parts) + ".",
        len(evidence),
        sufficient,
        stale,
        failed,
        inconclusive,
        last_test,
    )


# ---------------------------------------------------------------------------
# Running tests
# ---------------------------------------------------------------------------
@dataclass
class TestExecution:
    control_id: str
    test_type: str
    procedure: str
    result: TestResult
    rationale: str
    exceptions: int
    evidence_used: list[str]
    performed_by: str


def run_design_test(session: Session, control: Control, *, actor: str = "system") -> ControlTest:
    assessment = assess_design(control)
    if assessment.status == DesignEffectiveness.EFFECTIVE_BY_DESIGN:
        result = TestResult.PASS
    elif assessment.status in (DesignEffectiveness.PARTIALLY_ADDRESSED, DesignEffectiveness.UNABLE_TO_DETERMINE):
        result = TestResult.INCONCLUSIVE
    else:
        result = TestResult.FAIL
    procedure = (
        control.testing_procedure
        or "Design test: verify that the control definition contains an objective, activity, owner, frequency, "
        "preventive/detective classification, testing procedure and expected evidence."
    )
    test = ControlTest(
        test_uid=_uid("TST", session, ControlTest, "test_uid"),
        control_id=control.id,
        test_type="design",
        test_procedure=procedure,
        performed_by=actor,
        performed_at=_utcnow(),
        result=result,
        result_rationale=assessment.rationale,
        notes=f"Design completeness: {assessment.completeness:.0%}",
    )
    session.add(test)
    session.flush()
    control.design_effectiveness = assessment.status
    control.design_rationale = assessment.rationale
    return test


def run_operating_test(session: Session, control: Control, *, actor: str = "system") -> ControlTest:
    assessment = assess_operating(session, control)
    mapping = {
        OperatingEffectiveness.EFFECTIVE: TestResult.PASS,
        OperatingEffectiveness.PARTIALLY_EFFECTIVE: TestResult.PASS,
        OperatingEffectiveness.INEFFECTIVE: TestResult.FAIL,
        OperatingEffectiveness.INSUFFICIENT_EVIDENCE: TestResult.INCONCLUSIVE,
        OperatingEffectiveness.NOT_TESTED: TestResult.NOT_PERFORMED,
    }
    procedure = (
        control.testing_procedure
        or "Operating test: verify that current, approved evidence exists for the control period and that "
        "no linked test recorded a failure."
    )
    test = ControlTest(
        test_uid=_uid("TST", session, ControlTest, "test_uid"),
        control_id=control.id,
        test_type="operating",
        test_procedure=procedure,
        performed_by=actor,
        performed_at=_utcnow(),
        result=mapping[assessment.status],
        result_rationale=assessment.rationale,
        notes=f"Evidence artefacts: {assessment.evidence_count}; sufficient: {assessment.sufficient_evidence}",
    )
    session.add(test)
    session.flush()
    control.operating_effectiveness = assessment.status
    control.operating_rationale = assessment.rationale
    control.last_tested_at = test.performed_at
    return test


def _uid(prefix: str, session: Session, model: Any, column: str) -> str:
    from datetime import datetime
    import secrets

    for _ in range(10):
        candidate = f"{prefix}-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
        if not session.scalar(select(getattr(model, column)).where(getattr(model, column) == candidate).limit(1)):
            return candidate
    return f"{prefix}-{secrets.token_hex(8).upper()}"  # pragma: no cover


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


__all__ = [
    "assess_design",
    "assess_operating",
    "assess_evidence",
    "run_design_test",
    "run_operating_test",
    "DesignAssessment",
    "OperatingAssessment",
    "EvidenceAssessment",
]
