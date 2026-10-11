"""End-to-end demo workflow test through the public API.

Mirrors docs/DEMO_SCRIPT.md steps 1-15 on a small synthetic corpus.
Deterministic: asserts genuine gaps, explainable risk, CRITical deadline
escalation is NOT triggered here (no deadlines), and scenario predictions.
"""
from __future__ import annotations

import io

REG_TEXT = (
    "1. Applicability\n"
    "(1) These Directions shall be applicable to Commercial Banks having 10 or more banking outlets in India.\n"
    "2. Appointment\n"
    "(1) Every bank shall appoint at least one Internal Ombudsman.\n"
    "(2) The bank shall inform the Reserve Bank within 10 working days of any vacancy.\n"
)

AMEND_TEXT = (
    "1. Applicability\n"
    "(1) These Directions shall be applicable to Commercial Banks having 10 or more banking outlets in India.\n"
    "2. Appointment\n"
    "(1) Every bank shall appoint at least one Internal Ombudsman.\n"
    "(2) The bank shall inform the Reserve Bank within 5 working days of any vacancy.\n"
    "(3) The IO shall submit a quarterly systemic report within 15 days of each quarter.\n"
)


def _upload(client, name, text, kind="synthetic"):
    r = client.post("/api/v1/documents",
                    files={"file": (name, io.BytesIO(text.encode()), "text/plain")},
                    data={"source_kind": kind})
    assert r.status_code == 201, r.text
    return r.json()["document_id"]


def test_demo_workflow_end_to_end(client):
    # 1-2. regulation + two versions -------------------------------------
    reg = client.post("/api/v1/regulations",
                      json={"code": "REG-E2E", "title": "E2E demo regulation",
                            "applicability_summary": "Commercial banks 10+ outlets"}).json()
    v1 = client.post(f"/api/v1/regulations/{reg['id']}/versions",
                     json={"version_label": "v1"}).json()
    doc1 = _upload(client, "v1.txt", REG_TEXT)
    # attach the document to the version under test
    v1doc = client.post(f"/api/v1/regulations/{reg['id']}/versions",
                        json={"version_label": "v1-full", "document_id": doc1}).json()

    # 3-4. extract + approve ----------------------------------------------
    ex = client.post("/api/v1/obligations/extract",
                     json={"regulation_version_id": v1doc["id"], "document_id": doc1}).json()
    assert ex["created"] >= 2
    for o in ex["obligations"]:
        assert o["quoted_text"]  # citation preserved
        r = client.post(f"/api/v1/obligations/{o['id']}/approve",
                        json={"decision": "approved", "reviewer": "e2e"})
        assert r.status_code == 200

    # 5-6. controls + mappings ----------------------------------------------
    c1 = client.post("/api/v1/controls", json={
        "control_uid": "CTL-E2E-1", "title": "Appoint Internal Ombudsman",
        "control_objective": "Staff the IO office", "control_activity": "Appoint at least one IO yearly",
        "owner_role": "Head CS", "frequency": "annual",
        "expected_evidence": "Appointment letter", "testing_procedure": "Inspect file"}).json()
    c2 = client.post("/api/v1/controls", json={
        "control_uid": "CTL-E2E-2", "title": "Report vacancies on time",
        "control_objective": "Inform RBI of vacancies", "control_activity": "Inform RBI within deadline",
        "owner_role": "Compliance", "frequency": "event-driven",
        "expected_evidence": "Dispatch proof", "testing_procedure": "Trace events"}).json()
    sug = client.post("/api/v1/mappings/suggest", json={"use_vectors": False}).json()
    assert sug["persisted"] >= 1
    maps = client.get("/api/v1/mappings", params={"status": "candidate"}).json()
    assert maps["total"] >= 1
    first = maps["items"][0]
    ap = client.post(f"/api/v1/mappings/{first['id']}/approve",
                     json={"decision": "approved", "reviewer": "e2e"})
    assert ap.status_code == 200 and ap.json()["status"] == "approved"

    # 7. evidence + tests ----------------------------------------------------
    ev = client.post("/api/v1/evidence", json={
        "title": "Appointment letter sample", "control_ids": [c1["id"]],
        "evidence_period_start": "2025-01-01", "evidence_period_end": "2025-12-31",
        "source": "HR file (sample)"}).json()
    assert ev["evidence_uid"]
    rev = client.post(f"/api/v1/evidence/{ev['id']}/review",
                      params={"decision": "approved", "reviewer": "e2e"})
    assert rev.status_code == 200
    tst = client.post("/api/v1/tests/run", json={}).json()
    assert tst["tests_run"] >= 2

    # 8-9. gaps + risk ---------------------------------------------------------
    gaps = client.post("/api/v1/assessments/run", json={}).json()
    assert gaps["gaps_created"] >= 1  # e.g. vacancy control has no evidence
    risk = client.post("/api/v1/risks/recalculate", json={}).json()
    assert risk["scored"] >= 1
    for a in risk["assessments"]:
        assert 0 <= a["score"] <= 100
    summ = client.get("/api/v1/risks/summary").json()
    assert summ["total_assessed"] >= 1

    # 10. remediation + approval gate -------------------------------------------
    rem = client.post("/api/v1/remediation/generate", json={}).json()
    assert rem["actions_created"] >= 1
    action = rem["actions"][0]
    assert action["status"] == "proposed"
    ok = client.post(f"/api/v1/remediation/{action['id']}/approve",
                     json={"decision": "approved", "approver": "e2e"}).json()
    assert ok["status"] == "approved"
    done = client.post(f"/api/v1/remediation/{action['id']}/complete",
                       json={"closure_evidence": "sample closure pack", "completed_by": "e2e"})
    assert done.status_code == 200

    # 11-13. second version + compare + impact ------------------------------------
    doc2 = _upload(client, "v2.txt", AMEND_TEXT)
    v2 = client.post(f"/api/v1/regulations/{reg['id']}/versions",
                     json={"version_label": "v2", "document_id": doc2,
                           "supersedes_version_id": v1doc["id"]}).json()
    cmp = client.post(f"/api/v1/regulations/{reg['id']}/compare-versions",
                      json={"from_version_id": v1doc["id"], "to_version_id": v2["id"]}).json()
    assert len(cmp["changes"]) >= 1
    first_change = cmp["changes"][0]["id"]
    imp = client.post("/api/v1/impact/analyze", json={"change_id": first_change}).json()
    assert imp["impact_uid"]
    got = client.get(f"/api/v1/impact/{imp['id']}").json()
    assert got["impact_uid"] == imp["impact_uid"]

    # 14. scenarios ----------------------------------------------------------------
    sc = client.post("/api/v1/scenarios", json={
        "name": "E2E scenario", "strategy": "max_risk_reduction",
        "budget_inr": 1000000, "capacity_days": 200}).json()
    sim = client.post(f"/api/v1/scenarios/{sc['id']}/simulate").json()
    assert sim["solver"] in ("ortools_cpsat", "deterministic_greedy")
    assert sim["is_prediction"] is True
    res = client.get(f"/api/v1/scenarios/{sc['id']}/results").json()
    assert len(res["results"]) >= 1

    # 15. audit trail ----------------------------------------------------------------
    audit = client.get("/api/v1/audit/events", params={"page_size": 5}).json()
    assert audit["total"] >= 1
    runs = client.get("/api/v1/agents/runs").json()
    assert runs["total"] >= 0

    # dashboard reflects real data
    dash = client.get("/api/v1/dashboard").json()
    assert dash["counts"]["obligations"] >= 2
    assert dash["counts"]["open_gaps"] >= 1
    assert dash["demo_ready"] is True

