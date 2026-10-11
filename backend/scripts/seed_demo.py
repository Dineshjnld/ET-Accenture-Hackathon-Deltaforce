"""ReguLens AI demo seed — builds the complete, reproducible demo workflow.

Covers the demo script steps 1-15:
  official RBI PDFs (2023 + 2026) + synthetic policy/control matrix +
  synthetic simulated amendment -> obligations -> approvals -> applicability ->
  mappings -> evidence/tests -> gaps -> risk -> remediation ->
  version compare -> impact -> scenarios -> audit trail.

Idempotent: re-running without --reset detects the existing demo regulation
and exits. Use --reset to TRUNCATE all domain tables and rebuild.

Usage (from backend/):
    python scripts/seed_demo.py [--reset]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (register tables)
from app.config import settings
from app.db import get_engine, session_scope
from app.models.controls import Control, ObligationControlMapping, Policy, PolicyVersion
from app.models.core import (
    AutomationLevel,
    ControlType,
    DocumentSourceKind,
    EvidenceValidity,
    MappingStatus,
    RemediationStatus,
    ReviewStatus,
    TestResult,
    enum_value,
)
from app.models.evidence import ControlTest, Evidence, EvidenceControlLink
from app.models.identity import BusinessUnit, Organization, Role, User
from app.models.obligations import Obligation, RegulatoryChange
from app.models.regulatory import Document, Regulation, RegulationVersion, RegulatorySource
from app.models.remediation import ComplianceGap, RemediationAction, RemediationScenario

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL = REPO_ROOT / "data" / "samples" / "official_rbi"
SYNTHETIC = REPO_ROOT / "data" / "samples" / "synthetic"

REG_CODE = "RBI-IO-OMNIBUS"
ACTOR = "seed.demo"


def reset_all() -> None:
    from sqlalchemy import text

    engine = get_engine()
    tables = [
        "scenario_results", "remediation_scenarios", "remediation_dependencies",
        "remediation_actions", "risk_assessments", "compliance_gaps",
        "test_results", "control_tests", "evidence_control_links", "evidence",
        "obligation_control_mappings", "control_versions", "controls",
        "policy_versions", "policies", "applicability_assessments",
        "applicability_rules", "impact_assessments", "regulatory_changes",
        "obligations", "regulation_versions", "regulations",
        "regulatory_sources", "document_chunks", "document_versions",
        "documents", "agent_events", "agent_runs", "audit_events",
        "approvals", "users", "roles", "business_units", "organizations",
    ]
    with engine.begin() as conn:
        if settings.is_sqlite:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            for t in tables:
                conn.execute(text(f"DELETE FROM {t}"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
        else:
            conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    print("reset: truncated all domain tables")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if args.reset:
        reset_all()

    with session_scope() as session:
        from sqlalchemy import select

        existing = session.scalar(select(Regulation).where(Regulation.code == REG_CODE))
        if existing is not None and not args.reset:
            print(f"demo already seeded (regulation {REG_CODE}). Use --reset to rebuild.")
            return

        # -- 1. Organisation -------------------------------------------
        role = session.scalar(select(Role).where(Role.name == "compliance_officer"))
        if role is None:
            role = Role(name="compliance_officer", description="Compliance officer with review/approve rights",
                        permissions=json.dumps(["review", "approve", "close_gap"]))
            session.add(role)
            session.flush()
        org = Organization(
            name="Demo Metro Bank (fictional)", slug="demo-metro-bank",
            entity_type="scheduled_commercial_bank", jurisdiction="IN",
            total_assets_inr_crore=85000, banking_outlets_in_india=120,
            has_internal_ombudsman=True,
            notes="SYNTHETIC demo entity. Not a real bank.",
        )
        session.add(org)
        session.flush()
        bu_retail = BusinessUnit(organization_id=org.id, name="Retail Banking", code="RETAIL")
        bu_comp = BusinessUnit(organization_id=org.id, name="Compliance", code="COMPLIANCE")
        session.add_all([bu_retail, bu_comp])
        session.flush()
        user = User(username="compliance.officer", display_name="Compliance Officer",
                    role_id=role.id, organization_id=org.id)
        session.add(user)
        session.flush()

        # -- 2. Regulatory source + regulation --------------------------
        source = RegulatorySource(
            name="Reserve Bank of India — Master Directions",
            authority="Reserve Bank of India", jurisdiction="IN",
            source_url="https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?did=338",
            source_type="master_direction",
            description="Official RBI Master Directions index (Consumer Education and Protection).",
            retrieval_method="manual_download_single_file",
            retrieved_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
        reg = Regulation(
            source_id=source.id, code=REG_CODE,
            title="RBI Internal Ombudsman Directions (2023 omnibus + 2026 Commercial Banks + simulated amendment)",
            category="Consumer Education and Protection",
            regulator="Reserve Bank of India", jurisdiction="IN",
            applicability_summary="Commercial banks with 10+ outlets; see applicability rules.",
        )
        session.add(reg)
        session.flush()

        # -- 3. Ingest documents ----------------------------------------
        from app.services.ingestion import ingest_path

        def ingest(path: Path, kind: str, **kw) -> Document:
            res = ingest_path(
                session, path=path, original_filename=path.name,
                content_type="application/octet-stream", actor=ACTOR,
                source_kind=kind, version_label="v1", **kw,
            )
            doc = session.get(Document, res.document_id)
            print(f"ingested {path.name}: chunks={res.chunk_count} ocr={res.ocr_used} warnings={len(res.warnings)}")
            return doc

        doc2023 = ingest(
            OFFICIAL / "RBI_Internal_Ombudsman_Directions_2023.pdf", "official",
            source_url="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/108MDINTERNALOMBUDSMANCC05402F77BE4F229B59877F341386A4.PDF",
            publisher="Reserve Bank of India", published_at=date(2023, 12, 29),
            jurisdiction="IN", reference_number="RBI/CEPD/2023-24/108",
            document_type="master_direction",
        )
        doc2026 = ingest(
            OFFICIAL / "RBI_Internal_Ombudsman_Directions_2026_CommercialBanks.pdf", "official",
            source_url="https://rbidocs.rbi.org.in/rdocs/notification/PDFs/380MD14012026DAD56B73DC944F02AEA4530EC779AD7F.PDF",
            publisher="Reserve Bank of India", published_at=date(2026, 1, 14),
            jurisdiction="IN", reference_number="RBI/CEPD/2025-26/381",
            document_type="master_direction",
        )
        doc_policy = ingest(
            SYNTHETIC / "internal_grievance_policy_v3.txt", "synthetic",
            publisher="Demo Metro Bank (fictional)", published_at=date(2025, 4, 1),
            jurisdiction="IN", document_type="internal_policy",
        )
        doc_amend = ingest(
            SYNTHETIC / "simulated_amendment_2026a.txt", "synthetic",
            publisher="ReguLens demo harness (fictional)", published_at=date(2026, 3, 1),
            jurisdiction="IN", document_type="simulated_amendment",
        )

        v2023 = RegulationVersion(regulation_id=reg.id, version_label="2023-omnibus",
                                  published_at=date(2023, 12, 29), effective_from=date(2023, 12, 29),
                                  document_id=doc2023.id, is_current=False,
                                  notes="Official: Master Direction — RBI (Internal Ombudsman for Regulated Entities) Directions, 2023")
        v2026 = RegulationVersion(regulation_id=reg.id, version_label="2026-commercial-banks",
                                  published_at=date(2026, 1, 14), effective_from=date(2026, 1, 14),
                                  document_id=doc2026.id, supersedes_version_id=None, is_current=True,
                                  notes="Official: RBI (Commercial Banks - Internal Ombudsman) Directions, 2026")
        vamend = RegulationVersion(regulation_id=reg.id, version_label="2026-simulated-amendment",
                                   published_at=date(2026, 3, 1), effective_from=date(2026, 4, 1),
                                   document_id=doc_amend.id, is_current=False,
                                   notes="SYNTHETIC simulated amendment for change-impact demo only")
        session.add_all([v2023, v2026, vamend])
        session.flush()
        v2026.supersedes_version_id = v2023.id
        session.flush()

        # -- 4. Extract obligations --------------------------------------
        from app.services.obligations import persist_obligations

        obl2026 = persist_obligations(session, regulation_version=v2026, document=doc2026)
        obl2023 = persist_obligations(session, regulation_version=v2023, document=doc2023)
        oblamend = persist_obligations(session, regulation_version=vamend, document=doc_amend)
        print(f"extracted: 2026={len(obl2026)} 2023={len(obl2023)} amendment={len(oblamend)}")

        # Approve a deterministic subset: top-confidence from current version + a few from 2023
        def approve(obls: list[Obligation], n: int, reviewer: str = "compliance.officer") -> list[Obligation]:
            from app.services.audit import record_approval

            ranked = sorted(obls, key=lambda o: (-(o.extraction_confidence or 0), o.obligation_uid))[:n]
            for o in ranked:
                before = enum_value(o.review_status)
                o.review_status = ReviewStatus.APPROVED
                o.reviewed_by = reviewer
                o.reviewed_at = datetime.now(timezone.utc)
                record_approval(session, entity_type="obligation", entity_id=o.id,
                                decision="approved", decided_by=reviewer,
                                reason="Seed: deterministic top-confidence subset approved for demo.",
                                before_status=before, after_status="approved")
            session.flush()
            return ranked

        approved2026 = approve(obl2026, 14)
        approved2023 = approve(obl2023, 6)
        print(f"approved: 2026={len(approved2026)} 2023={len(approved2023)}")

        # -- 5. Applicability --------------------------------------------
        from app.services.applicability import assess_obligation, seed_default_rules

        seed_default_rules(session)
        counts = {"yes": 0, "no": 0, "unknown": 0}
        for o in approved2026 + approved2023:
            out = assess_obligation(session, o, organization=org, business_unit=bu_retail)
            counts[out.applicable] = counts.get(out.applicable, 0) + 1
        print(f"applicability: {counts}")

        # -- 6. Policy + controls -----------------------------------------
        policy = Policy(code="POL-GRIEV-003", title="Customer Grievance Redressal & IO Operations Manual (SAMPLE)",
                        owner_role="Head of Customer Service", category="grievance_redressal",
                        description="SYNTHETIC sample policy. Not a real bank policy.",
                        source_document_id=doc_policy.id)
        session.add(policy)
        session.flush()
        pver = PolicyVersion(policy_id=policy.id, version_label="v3.0",
                             effective_from=date(2025, 4, 1),
                             content_summary="Sample IO operations manual v3.0 (synthetic).",
                             approved_by="Board CSC (fictional)", approved_at=datetime.now(timezone.utc),
                             status="approved")
        session.add(pver)
        session.flush()

        def ctl(uid: str, title: str, bu: BusinessUnit | None, **kw) -> Control:
            c = Control(control_uid=uid, title=title,
                        policy_version_id=pver.id,
                        business_unit_id=bu.id if bu else None,
                        extraction_method="seed", is_synthetic=True, **kw)
            session.add(c)
            return c

        controls = {}
        rows = list(csv.DictReader((SYNTHETIC / "control_matrix_io.csv").open(encoding="utf-8")))
        for row in rows:
            uid = row["control_uid"].strip()
            if uid == "CTL-IO-TRAINING":
                controls[uid] = ctl(uid, row["title"], bu_comp)  # intentionally incomplete
                continue
            controls[uid] = ctl(
                uid, row["title"], bu_comp,
                control_objective=row["objective"] or None,
                control_activity=row["activity"] or None,
                owner_role=row["owner_role"] or None, frequency=row["frequency"] or None,
                control_type=row["control_type"] or "unknown",
                automation=row["automation"] or "unknown",
                expected_evidence=row["expected_evidence"] or None,
                testing_procedure=row["testing_procedure"] or None,
                source_document_id=doc_policy.id,
            )
        # one control deliberately not linked to any policy version (coverage gap)
        orphan = Control(control_uid="CTL-IO-ADHOC", title="Ad-hoc complaint handling (unlinked sample)",
                         business_unit_id=bu_retail.id, control_objective="Handle overflow complaints",
                         control_activity="Assign overflow complaints to available officers",
                         owner_role="Branch Manager", frequency="ongoing",
                         control_type=ControlType.DETECTIVE, extraction_method="seed",
                         is_synthetic=True)
        session.add(orphan)
        session.flush()
        from app.services.assessment import assess_design

        for c in list(controls.values()) + [orphan]:
            d = assess_design(c)
            c.design_effectiveness = d.status
            c.design_rationale = d.rationale
        session.flush()
        print(f"controls: {len(controls) + 1} (incl. 1 incomplete + 1 orphan)")

        # -- 7. Mappings: suggest, approve a subset ------------------------
        from app.services.mapping import persist_candidates, suggest_mappings

        approved_all = approved2026 + approved2023
        total_cand = 0
        first_choice: dict[str, ObligationControlMapping] = {}
        for o in approved_all:
            cands = suggest_mappings(session, o, use_vectors=False)
            created = persist_candidates(session, o, cands, actor=ACTOR)
            total_cand += len(created)
            if created:
                first_choice[o.id] = sorted(created, key=lambda m: -m.confidence)[0]
        # approve mappings for the first 6 obligations only -> rest stay candidate/unmapped
        from app.services.audit import record_approval as _ra

        for i, o in enumerate(approved_all[:6]):
            m = first_choice.get(o.id)
            if m is None:
                continue
            before = enum_value(m.status)
            m.status = MappingStatus.APPROVED
            m.reviewer_decision = "approved"
            m.reviewed_by = "compliance.officer"
            m.reviewed_at = datetime.now(timezone.utc)
            _ra(session, entity_type="obligation_control_mapping", entity_id=m.id,
                decision="approved", decided_by="compliance.officer",
                reason="Seed: approved for demo traceability.", before_status=before,
                after_status="approved")
        session.flush()
        print(f"mappings: candidates={total_cand}, approved=up to 6")

        # -- 8. Evidence + tests --------------------------------------------
        def ev(uid: str, title: str, start: date, end: date, collected: date, src: str) -> Evidence:
            e = Evidence(evidence_uid=uid, title=title, evidence_period_start=start,
                         evidence_period_end=end, collection_date=collected, source=src,
                         review_status=ReviewStatus.PENDING, is_synthetic=True,
                         uploaded_by="compliance.officer")
            session.add(e)
            return e

        e_appoint = ev("EVD-APPOINT-2025", "CSC minutes + IO appointment letter (Q1 2025 sample)",
                       date(2025, 1, 1), date.today() - timedelta(days=30), date.today() - timedelta(days=20),
                       "Board CSC secretariat (sample)")
        e_vacancy = ev("EVD-VACANCY-2024", "RBI vacancy intimation dispatch (H1 2024 sample)",
                       date(2024, 1, 1), date(2024, 6, 30), date(2024, 7, 5),
                       "Compliance dispatch register (sample)")
        e_escalate = ev("EVD-ESCALATE-2025", "Escalation register extract (current quarter sample)",
                        date.today() - timedelta(days=100), date.today() - timedelta(days=10),
                        date.today() - timedelta(days=5), "Grievance system export (sample)")
        session.flush()
        session.add_all([
            EvidenceControlLink(evidence_id=e_appoint.id, control_id=controls["CTL-IO-APPOINT"].id, created_by=ACTOR),
            EvidenceControlLink(evidence_id=e_vacancy.id, control_id=controls["CTL-IO-VACANCY"].id, created_by=ACTOR),
            EvidenceControlLink(evidence_id=e_escalate.id, control_id=controls["CTL-IO-ESCALATE"].id, created_by=ACTOR),
        ])
        session.flush()
        # reviewer approves the appointment evidence as sufficient
        e_appoint.review_status = ReviewStatus.APPROVED
        e_appoint.validity_status = EvidenceValidity.SUFFICIENT
        e_appoint.validity_rationale = "Seed: reviewed and marked sufficient (fresh period, approved)."
        _ra(session, entity_type="evidence", entity_id=e_appoint.id, decision="approved",
            decided_by="compliance.officer", reason="Seed review.", before_status="pending",
            after_status="approved")
        session.flush()

        from app.services.assessment import run_design_test, run_operating_test

        for c in list(controls.values()) + [orphan]:
            run_design_test(session, c, actor=ACTOR)
            run_operating_test(session, c, actor=ACTOR)
        # one genuine failed operating test (determinate failure)
        fail_test = ControlTest(
            test_uid="TST-SEED-FAIL-01", control_id=controls["CTL-IO-VACANCY"].id,
            test_type="operating",
            test_procedure="Seed: re-perform vacancy intimation timeliness for H1 2024 sample.",
            performed_by="internal.audit.sample", performed_at=datetime.now(timezone.utc),
            result=TestResult.FAIL,
            result_rationale="Seed: 2 of 5 sampled vacancy intimations exceeded the 10-working-day deadline.",
            exceptions_found=2, notes="SYNTHETIC sample test result for demo.",
            is_synthetic=True,
        )
        session.add(fail_test)
        session.flush()
        print("evidence + tests done (incl. 1 synthetic FAIL)")

        # -- 9. Gaps + risk + remediation ------------------------------------
        from app.services.gaps import detect_gaps

        gaps = detect_gaps(session)
        print(f"gaps detected (round 1): {len(gaps)}")
        from app.services.risk import recalculate_all

        risks = recalculate_all(session, actor=ACTOR)
        print(f"risk assessments: {len(risks)}")
        from app.services.remediation import generate_for_gaps, set_dependencies

        actions = generate_for_gaps(session)
        # deterministic cost assumption: duration_days * 10,000 INR where known else 50,000
        for a in actions:
            if a.estimated_cost_inr is None:
                base = (a.estimated_duration_days or 0) * 10000 or 50000
                a.estimated_cost_inr = float(base)
                a.cost_is_estimate = True
                a.uncertainty_notes = ((a.uncertainty_notes or "") + " Cost assumed at INR 10,000 per estimated day for demo planning.").strip()
        session.flush()
        # approve one action with a past deadline -> overdue demo path
        if actions:
            first = sorted(actions, key=lambda a: a.action_uid)[0]
            first.status = RemediationStatus.APPROVED
            first.approved_by = "compliance.officer"
            first.approved_at = datetime.now(timezone.utc)
            first.deadline = date.today() - timedelta(days=3)
            session.flush()
            gaps2 = detect_gaps(session)
            print(f"gaps detected (round 2, overdue path): {len(gaps2)}")
            recalculate_all(session, actor=ACTOR)
            # one precedence dependency for the optimiser demo
            if len(actions) >= 2:
                ordered = sorted(actions, key=lambda a: a.action_uid)
                set_dependencies(session, ordered[1].id, [ordered[0].id])
        session.flush()
        print(f"remediation actions: {len(actions)}")

        # -- 10. Version compare + impact -------------------------------------
        from app.services.diffing import compare_versions

        _, changes_amend = compare_versions(
            session, regulation_id=reg.id, from_version_id=v2026.id,
            to_version_id=vamend.id, actor=ACTOR, correlation_id="seed-amend")
        _, changes_real = compare_versions(
            session, regulation_id=reg.id, from_version_id=v2023.id,
            to_version_id=v2026.id, actor=ACTOR, correlation_id="seed-real")
        print(f"changes: amendment={len(changes_amend)} real2023_2026={len(changes_real)}")
        from app.services.impact import analyze_impact

        def _priority(ch: RegulatoryChange) -> int:
            order = {"deadline_change": 0, "threshold_change": 1, "applicability_change": 2,
                     "modified": 3, "added": 4, "removed": 5}
            return order.get(enum_value(ch.change_type), 9)

        impacts = []
        for ch in sorted(changes_amend, key=_priority)[:4] + sorted(changes_real, key=_priority)[:2]:
            impacts.append(analyze_impact(session, ch.id, actor=ACTOR))
        print(f"impact assessments: {len(impacts)}")

        # -- 11. Scenarios ------------------------------------------------------
        def scen(uid: str, name: str, strategy: str, desc: str, budget, cap, horizon) -> RemediationScenario:
            s = RemediationScenario(scenario_uid=uid, name=name, strategy=strategy,
                                    description=desc, budget_inr=budget, capacity_days=cap,
                                    horizon_days=horizon, created_by="compliance.officer",
                                    assumptions=json.dumps({
                                        "demo": True,
                                        "cost_note": "Unknown costs assumed at INR 10,000 per estimated day.",
                                    }))
            session.add(s)
            return s

        s1 = scen("SCN-DEMO-A", "Scenario A — highest risk first", "max_risk_reduction",
                  "Maximise expected risk reduction within budget.", 500000, 120, 180)
        s2 = scen("SCN-DEMO-B", "Scenario B — shared control efficiency", "shared_control",
                  "Prioritise shared controls covering several obligations.", 300000, 120, 180)
        s3 = scen("SCN-DEMO-C", "Scenario C — deadline-first", "deadline_first",
                  "Prioritise obligations with approaching deadlines.", None, 200, 90)
        s4 = scen("SCN-DEMO-D", "Scenario D — unconstrained review", "custom",
                  "Custom priority order without budget cap (review baseline).", None, None, None)
        session.flush()
        from app.services.scenarios import run_scenario

        for s in (s1, s2, s3, s4):
            r = run_scenario(session, s, actor=ACTOR)
            print(f"{s.scenario_uid}: solver={r.solver} objective={r.objective_value} "
                  f"cost={r.projected_cost_inr} days={r.projected_duration_days}")

        # -- 12. Validation snapshot ---------------------------------------------
        from app.services.contradiction import detect_all as _contra
        from app.services.cross_regulation import build_report as _cross

        contra = _contra(session)
        cross = _cross(session)
        print(f"contradictions={len(contra.findings)} similar_pairs={len(cross.pairs)}")

        from sqlalchemy import func, select as _select

        summary = {
            "regulation": reg.code,
            "obligations_total": session.scalar(_select(func.count()).select_from(Obligation)),
            "obligations_approved": session.scalar(_select(func.count()).select_from(Obligation).where(Obligation.review_status == ReviewStatus.APPROVED)),
            "gaps_open": session.scalar(_select(func.count()).select_from(ComplianceGap).where(ComplianceGap.status == "open")),
            "actions": session.scalar(_select(func.count()).select_from(RemediationAction)),
            "changes": session.scalar(_select(func.count()).select_from(RegulatoryChange)),
            "impacts": len(impacts),
        }
        print("SEED SUMMARY: " + json.dumps(summary))
        print("DONE — demo_ready=true")


if __name__ == "__main__":
    main()
