"""Tests for deliverable artifact storage, report synthesis, and 5-tier verification rubric (PRD Sections 10.3, 10.5)."""


def test_artifact_creation_storage_and_download(api_client, accounts):
    owner = accounts("art-author@example.com")
    ws_resp = owner.post("/api/v1/workspaces", json={"name": "Artifact WS"})
    assert ws_resp.status_code == 201
    ws_id = ws_resp.json()["id"]

    # Synthesize deliverable
    synth_resp = owner.post(
        "/api/v1/reports/synthesize",
        json={
            "workspace_id": ws_id,
            "title": "Initial Findings Deliverable",
        },
    )
    assert synth_resp.status_code == 200, synth_resp.text
    data = synth_resp.json()
    assert data["status"] == "synthesized"
    artifacts = data["artifacts"]
    assert len(artifacts) == 3

    md_art = next(a for a in artifacts if a["artifact_type"] == "report_markdown")
    art_id = md_art["id"]

    # 2. List artifacts via API
    resp = owner.get(f"/api/v1/artifacts?workspace_id={ws_id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 3
    assert any(it["id"] == art_id for it in items)

    # 3. Get artifact metadata
    meta_resp = owner.get(f"/api/v1/artifacts/{art_id}")
    assert meta_resp.status_code == 200
    assert meta_resp.json()["artifact_type"] == "report_markdown"

    # 4. Download artifact content
    dl_resp = owner.get(f"/api/v1/artifacts/{art_id}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"].startswith("text/markdown")
    assert "attachment; filename=" in dl_resp.headers["content-disposition"]
    assert "# Initial Findings Deliverable" in dl_resp.text


def test_synthesize_workflow_report_with_custom_sections(api_client, accounts):
    owner = accounts("scientist@example.com")
    ws_resp = owner.post("/api/v1/workspaces", json={"name": "Synthesis WS"})
    assert ws_resp.status_code == 201
    ws_id = ws_resp.json()["id"]

    # Submit workflow
    wf_resp = owner.post(
        "/api/v1/workflows",
        json={
            "workspace_id": ws_id,
            "prompt": "Inspect telemetry, compute summary, and verify output",
            "csv_text": "sensor,reading,unit\nA1,98.2,C\nA2,97.8,C",
            "planner_mode": "offline",
        },
    )
    assert wf_resp.status_code == 202
    wf_id = wf_resp.json()["workflow_id"]

    # Synthesize report for this workflow with custom section
    custom_section = {
        "id": "sec-custom-telemetry",
        "title": "Thermal Runaway Analysis",
        "content": "All sensors maintained operating temperatures under 100°C.",
        "section_type": "findings",
        "citations": [],
    }

    resp = owner.post(
        "/api/v1/reports/synthesize",
        json={
            "workspace_id": ws_id,
            "workflow_id": wf_id,
            "title": "Battery Thermal Analysis Report",
            "custom_sections": [custom_section],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "synthesized"
    artifacts = data["artifacts"]

    types = {a["artifact_type"] for a in artifacts}
    assert "report_markdown" in types
    assert "report_html" in types
    assert "summary_json" in types

    # Download HTML deliverable
    html_art = next(a for a in artifacts if a["artifact_type"] == "report_html")
    dl = owner.get(f"/api/v1/artifacts/{html_art['id']}/download")
    assert dl.status_code == 200
    assert "<!DOCTYPE html>" in dl.text
    assert "Thermal Runaway Analysis" in dl.text
    assert "Battery Thermal Analysis Report" in dl.text


def test_rubric_verification_pass_and_warnings(api_client, accounts):
    owner = accounts("auditor@example.com")
    ws_id = owner.post("/api/v1/workspaces", json={"name": "Rubric WS"}).json()["id"]

    # Synthesize deliverable with full sections
    synth = owner.post(
        "/api/v1/reports/synthesize",
        json={
            "workspace_id": ws_id,
            "title": "Comprehensive Verifiable Deliverable",
        },
    ).json()
    md_art = next(a for a in synth["artifacts"] if a["artifact_type"] == "report_markdown")
    art_id = md_art["id"]

    # Verify deliverable via rubric API
    resp = owner.post(
        "/api/v1/verification/verify",
        json={"workspace_id": ws_id, "artifact_id": art_id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["decision"] in ("PASS", "PASS_WITH_WARNINGS")
    assert data["confidence_score"] >= 0.70
    assert len(data["criteria"]) > 0

    # Retrieve verification report by ID
    get_resp = owner.get(f"/api/v1/verification/{data['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == data["id"]
    assert get_resp.json()["confidence_score"] == data["confidence_score"]


def test_rubric_verification_failure_on_failed_workflow(api_client, accounts):
    owner = accounts("tester@example.com")
    ws_id = owner.post("/api/v1/workspaces", json={"name": "Fail WS"}).json()["id"]

    # Create deliverable
    synth = owner.post(
        "/api/v1/reports/synthesize",
        json={"workspace_id": ws_id, "title": "Report for Failed Flow"},
    ).json()
    art_id = synth["artifacts"][0]["id"]

    # Verification with custom failing mandatory check
    resp = owner.post(
        "/api/v1/verification/verify",
        json={
            "workspace_id": ws_id,
            "artifact_id": art_id,
            "custom_checks": [
                {
                    "id": "chk_compliance_iso",
                    "category": "security_compliance",
                    "criterion": "ISO 27001 data sovereignty certification",
                    "status": "failed",
                    "is_mandatory": True,
                    "evidence": "Data sovereignty compliance check failed",
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] in ("REVISION_REQUIRED", "FAIL")
    assert any(
        c["id"] == "chk_compliance_iso" and c["status"] == "failed" for c in data["criteria"]
    )


def test_workspace_isolation_and_rbac(api_client, accounts):
    owner_a = accounts("alice@a.com")
    ws_a = owner_a.post("/api/v1/workspaces", json={"name": "Tenant A"}).json()["id"]

    owner_b = accounts("bob@b.com")
    ws_b = owner_b.post("/api/v1/workspaces", json={"name": "Tenant B"}).json()["id"]

    # Synthesize in Workspace A
    synth_a = owner_a.post(
        "/api/v1/reports/synthesize",
        json={"workspace_id": ws_a, "title": "Confidential Report A"},
    ).json()
    art_a_id = synth_a["artifacts"][0]["id"]

    # User B cannot read or download Artifact A (404)
    assert owner_b.get(f"/api/v1/artifacts/{art_a_id}").status_code == 404
    assert owner_b.get(f"/api/v1/artifacts/{art_a_id}/download").status_code == 404

    # Workspace B listing does not reveal Artifact A
    b_list = owner_b.get(f"/api/v1/artifacts?workspace_id={ws_b}").json()
    assert not any(a["id"] == art_a_id for a in b_list)

    # Add viewer to Workspace A
    viewer = accounts("charlie@viewer.com")
    owner_a.post(
        f"/api/v1/workspaces/{ws_a}/members",
        json={"email": "charlie@viewer.com", "role": "viewer"},
    )

    # Viewer can inspect and download artifact
    assert viewer.get(f"/api/v1/artifacts/{art_a_id}").status_code == 200
    assert viewer.get(f"/api/v1/artifacts/{art_a_id}/download").status_code == 200

    # Viewer cannot trigger synthesis (403) or verification (403)
    unauth_synth = viewer.post(
        "/api/v1/reports/synthesize",
        json={"workspace_id": ws_a, "title": "Unauthorized"},
    )
    assert unauth_synth.status_code == 403

    unauth_verify = viewer.post(
        "/api/v1/verification/verify",
        json={"workspace_id": ws_a, "artifact_id": art_a_id},
    )
    assert unauth_verify.status_code == 403
