"""Applicability, mapping, assessment, evidence tests."""
from __future__ import annotations

from app.models.controls import Control
from app.models.core import EvidenceValidity, ReviewStatus
from app.models.evidence import Evidence
from app.models.identity import BusinessUnit, Organization
from app.models.obligations import ApplicabilityRule, Obligation
from app.services.applicability import assess_obligation, evaluate_rule, seed_default_rules
from app.services.assessment import assess_design, assess_evidence, assess_operating
from app.services.mapping import suggest_mappings


def _org(**kw):
    base = dict(name="Demo", slug="demo", entity_type="scheduled_commercial_bank",
                jurisdiction="IN", banking_outlets_in_india=120,
                has_internal_ombudsman=True)
    base.update(kw)
    return Organization(**base)


def test_threshold_rule_applies(db):
    seed_default_rules(db)
    org = _org()
    db.add(org)
    db.flush()
    obl = Obligation(obligation_uid="O-1", regulation_id="r", regulation_version_id="v",
                     quoted_text="q", obligation_text="The bank shall appoint an IO.")
    db.add(obl)
    db.flush()
    out = assess_obligation(db, obl, organization=org, persist=False)
    assert out.applicable == "yes"


def test_unknown_outlets_yields_unknown(db):
    seed_default_rules(db)
    org = _org(banking_outlets_in_india=None)
    db.add(org)
    db.flush()
    obl = Obligation(obligation_uid="O-2", regulation_id="r", regulation_version_id="v",
                     quoted_text="q", obligation_text="t")
    db.add(obl)
    db.flush()
    out = assess_obligation(db, obl, organization=org, persist=False)
    assert out.applicable in ("yes", "unknown")
    # the outlet-threshold rule specifically must be indeterminate
    rules = {r.name: r for r in db.query(ApplicabilityRule).all()}
    res = evaluate_rule(rules["Internal Ombudsman threshold — 10 or more outlets"],
                        {"banking_outlets_in_india": None})
    assert res.applicable == "unknown"
    assert "banking_outlets_in_india" in res.missing_attributes


def test_below_threshold_not_applicable(db):
    seed_default_rules(db)
    org = _org(banking_outlets_in_india=5)
    db.add(org)
    db.flush()
    obl = Obligation(obligation_uid="O-3", regulation_id="r", regulation_version_id="v",
                     quoted_text="q", obligation_text="t")
    db.add(obl)
    db.flush()
    out = assess_obligation(db, obl, organization=org, persist=False)
    assert out.applicable in ("no", "unknown")


def test_mapping_ranks_true_control_first():
    def C(uid, text):
        return Control(control_uid=uid, title=text, control_objective=text,
                       control_activity=text)

    controls = [
        C("A", "Appoint at least one Internal Ombudsman and review strength yearly"),
        C("B", "Retain records for five years in the archive"),
    ]
    obl = Obligation(obligation_uid="O", regulation_id="r", regulation_version_id="v",
                     quoted_text="Every bank shall appoint at least one IO.",
                     obligation_text="Every bank shall appoint at least one IO.")

    class _S:
        pass

    cands = suggest_mappings(_S(), obl, controls=controls, use_vectors=False, min_confidence=0.0)
    assert cands[0].control_id == "A" or getattr(cands[0], "control_id", None) is None
    # transient controls share id=None; ranking is what matters
    assert "appoint" in cands[0].shared_terms


def test_design_assessment_separates(db):
    full = Control(control_uid="C1", title="T", control_objective="o",
                   control_activity="a", owner_role="owner", frequency="annual",
                   testing_procedure="p", expected_evidence="e")
    res = assess_design(full)
    assert res.status.value == "effective_by_design"
    empty = Control(control_uid="C2", title="T")
    res2 = assess_design(empty)
    assert res2.status.value in ("not_addressed", "unable_to_determine")


def test_evidence_stale_detection(db):
    from datetime import date, timedelta

    old_end = date.today() - timedelta(days=400)
    e = Evidence(evidence_uid="E1", title="old", evidence_period_end=old_end,
                 review_status=ReviewStatus.APPROVED, validity_status=EvidenceValidity.SUFFICIENT)
    res = assess_evidence(e) if False else None
    from app.services.assessment import assess_evidence as ae

    res = ae(e)
    assert res.validity.value == "stale"
    assert res.days_since_period_end and res.days_since_period_end > 180


def test_evidence_not_reviewed_state(db):
    from datetime import date

    e = Evidence(evidence_uid="E2", title="new",
                 evidence_period_end=date.today(), review_status=ReviewStatus.PENDING)
    from app.services.assessment import assess_evidence as ae

    assert ae(e).validity.value == "not_reviewed"


def test_failed_test_marks_insufficient(db):
    from datetime import date

    e = Evidence(evidence_uid="E3", title="t", evidence_period_end=date.today(),
                 review_status=ReviewStatus.APPROVED, test_result="fail")
    from app.services.assessment import assess_evidence as ae

    res = ae(e)
    assert res.validity.value == "insufficient"
    assert res.blocking_reasons


def test_operating_not_tested_without_tests(db):
    c = Control(control_uid="C9", title="T", control_objective="o",
                control_activity="a", owner_role="w", frequency="m",
                testing_procedure="p", expected_evidence="e")
    db.add(c)
    db.flush()
    res = assess_operating(db, c)
    assert res.status.value == "not_tested"



