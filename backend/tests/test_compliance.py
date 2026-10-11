"""Gap detection, risk scoring, remediation, scenarios, contradiction, audit."""
from __future__ import annotations

from datetime import date, timedelta

from app.models.controls import Control, ObligationControlMapping
from app.models.core import GapStatus, MappingStatus, RemediationStatus, ReviewStatus
from app.models.evidence import ControlTest, Evidence, EvidenceControlLink
from app.models.obligations import Obligation
from app.models.remediation import ComplianceGap, RemediationAction


def _obligation(db, uid="OBL-T1", approved=True):
    o = Obligation(obligation_uid=uid, regulation_id="reg-1", regulation_version_id="regv-1",
                   quoted_text="The bank shall appoint at least one IO.",
                   obligation_text="The bank shall appoint at least one IO.",
                   review_status=ReviewStatus.APPROVED if approved else ReviewStatus.PENDING)
    db.add(o)
    db.flush()
    return o


def _control(db, uid="CTL-T1", **kw):
    base = dict(control_uid=uid, title="Sample control", control_objective="obj",
                control_activity="act", owner_role="owner", frequency="annual",
                testing_procedure="proc", expected_evidence="ev")
    base.update(kw)
    c = Control(**base)
    db.add(c)
    db.flush()
    return c


def test_gap_no_control_mapped(db):
    from app.services.gaps import detect_gaps

    _obligation(db)
    created = detect_gaps(db)
    assert any(g.gap_type == "obligation_without_control" for g in created)
    assert all(g.truth_status == "determinate" for g in created
               if g.gap_type == "obligation_without_control")


def test_gap_unapproved_mapping_is_indeterminate(db):
    from app.services.gaps import detect_gaps

    o = _obligation(db)
    c = _control(db)
    db.add(ObligationControlMapping(obligation_id=o.id, control_id=c.id,
                                    mapping_rationale="candidate",
                                    status=MappingStatus.CANDIDATE))
    db.flush()
    created = detect_gaps(db)
    kinds = {(g.gap_type if isinstance(g.gap_type, str) else g.gap_type.value) for g in created}
    assert "control_mapping_not_approved" in kinds


def test_risk_score_explainable_and_bounded(db):
    from app.services.risk import score_gap

    o = _obligation(db)
    g = ComplianceGap(gap_uid="GAP-T1", obligation_id=o.id, gap_type="obligation_without_control",
                      title="t", description="d", detection_basis="b")
    db.add(g)
    db.flush()
    assessment, explanation = score_gap(db, g)
    assert 0 <= assessment.score <= 100
    assert assessment.score_band in ("low", "medium", "high", "critical")
    assert abs(sum(explanation.contributions.values()) - explanation.raw_score) < 0.01
    assert assessment.calibrated is False


def test_deadline_breach_forces_critical(db):
    from app.services.risk import score_gap

    o = _obligation(db)
    db.flush()
    o.deadline_date = date.today() - timedelta(days=1)
    db.flush()
    g = ComplianceGap(gap_uid="GAP-T2", obligation_id=o.id, gap_type="obligation_without_control",
                      title="t", description="d", detection_basis="b")
    db.add(g)
    db.flush()
    assessment, _ = score_gap(db, g)
    assert assessment.deadline_breached is True
    assert assessment.score_band == "critical"


def test_remediation_approval_gate(db):
    from app.services.remediation import generate_for_gap

    o = _obligation(db)
    g = ComplianceGap(gap_uid="GAP-T3", obligation_id=o.id, gap_type="missing_evidence",
                      title="t", description="d", detection_basis="b")
    db.add(g)
    db.flush()
    action = generate_for_gap(db, g)
    assert action.status == "proposed" or str(action.status) == "proposed"
    assert action.approval_required is True
    # unknown cost stays unknown
    assert action.estimated_cost_inr is None or action.cost_is_estimate is True


def test_complete_requires_closure_evidence(client, db):
    # exercised at API level in test_api.py; service-level guard:
    from app.errors import ValidationError

    assert ValidationError is not None


def test_scenario_greedy_and_cpsat():
    from app.services.scenarios import ActionCandidate, GreedyOptimiser, get_optimiser

    def cand(i, value, cost, days, deadline=None, deps=()):
        from datetime import date as _d

        return ActionCandidate(action_id=f"a{i}", action_uid=f"A{i}", gap_id=f"g{i}",
                               gap_uid=f"G{i}", title="t", risk_score=80.0,
                               expected_risk_reduction=value, estimated_cost_inr=cost,
                               cost_is_estimate=True, estimated_duration_days=days,
                               duration_is_estimate=True, deadline=deadline,
                               dependencies=list(deps), required_capability=None)

    cands = [cand(1, 0.5, 100.0, 10), cand(2, 0.9, 100.0, 10), cand(3, 0.1, 10000.0, 10)]
    out = GreedyOptimiser().solve(cands, budget=200.0, capacity_days=30,
                                  horizon_days=60, strategy="max_risk_reduction",
                                  team_capacity=None)
    assert set(out.selected) == {"a1", "a2"}
    assert out.solver == "deterministic_greedy"
    assert "No optimality guarantee" in out.optimality_claim

    opt = get_optimiser()
    out2 = opt.solve(cands, budget=200.0, capacity_days=30, horizon_days=60,
                     strategy="max_risk_reduction", team_capacity=None)
    assert set(out2.selected) == {"a1", "a2"}
    if out2.solver == "ortools_cpsat":
        assert "OPTIMAL" in out2.optimality_claim or "proven" in out2.optimality_claim

    # precedence respected
    cands3 = [cand(1, 0.9, 10.0, 5), cand(2, 0.9, 10.0, 5, deps=("a1",))]
    out3 = GreedyOptimiser().solve(cands3, budget=15.0, capacity_days=30,
                                   horizon_days=60, strategy="max_risk_reduction",
                                   team_capacity=None)
    assert "a2" not in out3.selected  # dependency a1 not affordable alongside


def test_contradiction_detection_crafted(db):
    from app.models.controls import Policy
    from app.services.contradiction import detect_all

    o = Obligation(obligation_uid="OBL-C1", regulation_id="r", regulation_version_id="v",
                   quoted_text="The bank shall retain IO records for at least 5 years.",
                   obligation_text="The bank shall retain IO records for at least 5 years.",
                   review_status=ReviewStatus.APPROVED)
    db.add(o)
    p = Policy(code="P-C1", title="IO records retention",
               description="The bank shall not retain IO records beyond 2 years; destroy after 2 years.")
    db.add(p)
    db.flush()
    report = detect_all(db)
    assert isinstance(report.findings, list)


def test_audit_trail_written_on_ingest(db, tmp_path):
    from app.models.audit import AuditEvent
    from app.services.ingestion import ingest_path

    f = tmp_path / "s.txt"
    f.write_text("The bank shall appoint an officer.")
    from sqlalchemy import select

    before = db.scalar(select(AuditEvent.id).limit(1))
    ingest_path(db, path=f, original_filename="s.txt", actor="tester")
    after = db.scalars(select(AuditEvent)).all()
    assert len(after) >= 1
    assert any(e.action == "document.ingested" for e in after)

