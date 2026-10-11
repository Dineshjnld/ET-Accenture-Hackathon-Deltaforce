"""API contract tests: health, validation, pagination, approvals, workflow."""
from __future__ import annotations

import io


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_system_status_reports_deterministic_mode(client):
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "deterministic"
    assert body["database"]["connected"] is True
    assert isinstance(body["warnings"], list) and body["warnings"]


def test_upload_rejects_bad_type(client):
    r = client.post("/api/v1/documents",
                    files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
    assert r.status_code in (400, 422)


def test_upload_rejects_empty(client):
    r = client.post("/api/v1/documents",
                    files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")})
    assert r.status_code in (400, 422)


def test_upload_txt_and_list(client):
    r = client.post("/api/v1/documents",
                    files={"file": ("policy.txt", io.BytesIO(b"The bank shall appoint an officer."), "text/plain")},
                    data={"source_kind": "internal"})
    assert r.status_code == 201, r.text
    doc_id = r.json()["document_id"]
    r2 = client.get("/api/v1/documents")
    assert r2.status_code == 200
    assert r2.json()["total"] >= 1
    r3 = client.get(f"/api/v1/documents/{doc_id}")
    assert r3.status_code == 200
    assert r3.json()["filename"] == "policy.txt"


def test_duplicate_upload_detected(client):
    payload = {"file": ("dup.txt", io.BytesIO(b"Same content here."), "text/plain")}
    r1 = client.post("/api/v1/documents", files=payload)
    assert r1.status_code == 201
    r2 = client.post("/api/v1/documents",
                     files={"file": ("dup.txt", io.BytesIO(b"Same content here."), "text/plain")})
    assert r2.status_code == 201
    assert r2.json()["duplicate_of"] == r1.json()["document_id"]


def test_regulation_crud_and_versions(client):
    r = client.post("/api/v1/regulations", json={"code": "REG-T1", "title": "Test regulation"})
    assert r.status_code == 201, r.text
    reg_id = r.json()["id"]
    r = client.post(f"/api/v1/regulations/{reg_id}/versions",
                    json={"version_label": "v1", "is_current": True})
    assert r.status_code == 201
    r = client.get(f"/api/v1/regulations/{reg_id}")
    assert r.status_code == 200
    assert len(r.json()["versions"]) == 1
    # duplicate code -> 409, no partial row
    r = client.post("/api/v1/regulations", json={"code": "REG-T1", "title": "Duplicate title here"})
    assert r.status_code == 409


def test_obligation_approve_requires_human(client):
    r = client.post("/api/v1/regulations", json={"code": "REG-T2", "title": "Test regulation two"})
    reg_id = r.json()["id"]
    v = client.post(f"/api/v1/regulations/{reg_id}/versions", json={"version_label": "v1"}).json()
    d = client.post("/api/v1/documents",
                    files={"file": ("reg.txt", io.BytesIO(b"(1) The bank shall appoint an officer."), "text/plain")},
                    data={"source_kind": "synthetic"}).json()
    ex = client.post("/api/v1/obligations/extract",
                     json={"regulation_version_id": v["id"], "document_id": d["document_id"]})
    assert ex.status_code == 200
    assert ex.json()["created"] >= 1
    obl_id = ex.json()["obligations"][0]["id"]
    assert ex.json()["obligations"][0]["review_status"] == "pending"
    ap = client.post(f"/api/v1/obligations/{obl_id}/approve",
                     json={"decision": "approved", "reviewer": "tester"})
    assert ap.status_code == 200
    assert ap.json()["review_status"] == "approved"


def test_llm_unavailable_fallback_is_honest(client):
    r = client.post("/api/v1/regulations", json={"code": "REG-T3", "title": "Test regulation three"})
    reg_id = r.json()["id"]
    v = client.post(f"/api/v1/regulations/{reg_id}/versions", json={"version_label": "v1"}).json()
    d = client.post("/api/v1/documents",
                    files={"file": ("r.txt", io.BytesIO(b"The bank shall retain records."), "text/plain")},
                    data={"source_kind": "synthetic"}).json()
    ex = client.post("/api/v1/obligations/extract",
                     json={"regulation_version_id": v["id"], "document_id": d["document_id"],
                           "use_llm": True})
    assert ex.status_code == 200
    body = ex.json()
    assert body["method"] == "deterministic"
    assert any("deterministic" in w for w in body["warnings"])


def test_pagination_envelope(client):
    for i in range(3):
        client.post("/api/v1/documents",
                    files={"file": (f"p{i}.txt", io.BytesIO(f"doc {i}".encode()), "text/plain")})
    r = client.get("/api/v1/documents", params={"page": 1, "page_size": 2})
    body = r.json()
    assert set(body) >= {"items", "total", "page", "page_size", "pages"}
    assert len(body["items"]) == 2 and body["total"] >= 3


def test_404_envelope(client):
    r = client.get("/api/v1/documents/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_agents_catalog_and_unknown_workflow(client):
    r = client.get("/api/v1/agents/catalog")
    assert r.status_code == 200
    assert len(r.json()[0]["agents"]) == 13
    r = client.post("/api/v1/agents/workflows/run",
                    json={"workflow": "nope", "params": {}})
    assert r.status_code == 422


def test_workflow_run_compliance_assessment(client):
    r = client.post("/api/v1/agents/workflows/run",
                    json={"workflow": "compliance_assessment", "params": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["run_uid"]
    assert body["status"] in ("succeeded", "partial", "failed")
