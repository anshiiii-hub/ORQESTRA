import pytest

from app.approvals import (
    ApprovalError,
    classify_tool,
    compute_arguments_hash,
    sanitize_arguments,
)


def test_fingerprint_hashing_and_argument_sanitization():
    params = {"filename": "export.csv", "secret_key": "supersecret123", "csv_data": "a,b\n1,2"}
    h1 = compute_arguments_hash(params)
    h2 = compute_arguments_hash(params)
    assert h1 == h2
    assert len(h1) == 64

    # Canonical order invariance
    params_reordered = {
        "csv_data": "a,b\n1,2",
        "filename": "export.csv",
        "secret_key": "supersecret123",
    }
    assert compute_arguments_hash(params_reordered) == h1

    # Sanitization masks sensitive keys
    sanitized = sanitize_arguments(params)
    assert sanitized["secret_key"] == "[REDACTED]"
    assert sanitized["filename"] == "export.csv"


def test_tool_classification_and_write_detection():
    write_tool = classify_tool("file.export_csv")
    assert write_tool["effect"] == "external_write"
    assert write_tool["requires_approval"] is True

    read_tool = classify_tool("knowledge.retrieve")
    assert read_tool["effect"] == "read_only"
    assert read_tool["requires_approval"] is False

    compute_tool = classify_tool("sandbox.python.execute")
    assert compute_tool["effect"] == "isolated_compute"
    assert compute_tool["requires_approval"] is False

    with pytest.raises(ApprovalError) as err:
        classify_tool("unregistered.dangerous_tool")
    assert err.value.code == "unregistered_tool"


def test_100_percent_approval_enforcement_for_external_writes(api_client, accounts):
    owner = accounts("appr-owner@example.com")
    workspace = owner.post("/api/v1/workspaces", json={"name": "Approvals WS"}).json()["id"]

    # 1. Attempt external write without approval -> Enters WAITING_APPROVAL (202)
    payload = {
        "workspace_id": workspace,
        "tool_name": "file.export_csv",
        "parameters": {"filename": "sales_q4.csv", "csv_content": "item,sales\nA,100\nB,200"},
    }
    resp = owner.post("/api/v1/tools/execute", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "waiting_approval"
    assert data["requires_approval"] is True
    approval_id = data["approval_id"]
    assert approval_id is not None

    # 2. Direct attempt with fake or pending approval -> 403 Forbidden
    unapproved = owner.post(
        "/api/v1/tools/execute",
        json={**payload, "approval_id": approval_id},
    )
    assert unapproved.status_code == 403
    assert "cannot execute" in unapproved.text


def test_approval_decision_workflow_and_fingerprint_tamper_prevention(api_client, accounts):
    owner = accounts("dec-owner@example.com")
    member = accounts("dec-member@example.com")
    workspace = owner.post("/api/v1/workspaces", json={"name": "Decision WS"}).json()["id"]
    owner.post(
        f"/api/v1/workspaces/{workspace}/members",
        json={"email": "dec-member@example.com", "role": "member"},
    )

    params = {"filename": "verified.csv", "csv_content": "id,name\n1,Alpha"}

    # Request tool write
    req_resp = member.post(
        "/api/v1/tools/execute",
        json={"workspace_id": workspace, "tool_name": "file.export_csv", "parameters": params},
    )
    assert req_resp.status_code == 202
    approval_id = req_resp.json()["approval_id"]

    # List pending approvals
    pending = owner.get(f"/api/v1/approvals?workspace_id={workspace}&status=pending")
    assert pending.status_code == 200
    assert len(pending.json()) >= 1
    assert pending.json()[0]["id"] == approval_id

    # Owner approves the action
    decision_resp = owner.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "reason": "Reviewed and verified safe"},
    )
    assert decision_resp.status_code == 200
    assert decision_resp.json()["status"] == "approved"
    assert decision_resp.json()["decision"] == "approved"

    # Caller tries to tamper with parameters after approval (fingerprint mismatch)
    tampered_params = {"filename": "malicious.csv", "csv_content": "DROP TABLE users;"}
    tampered_attempt = member.post(
        "/api/v1/tools/execute",
        json={
            "workspace_id": workspace,
            "tool_name": "file.export_csv",
            "parameters": tampered_params,
            "approval_id": approval_id,
        },
    )
    assert tampered_attempt.status_code == 422
    assert "fingerprint" in tampered_attempt.text

    # Caller executes with exact original parameters -> Success (200)
    exec_resp = member.post(
        "/api/v1/tools/execute",
        json={
            "workspace_id": workspace,
            "tool_name": "file.export_csv",
            "parameters": params,
            "approval_id": approval_id,
        },
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["status"] == "completed"
    assert exec_resp.json()["result"]["destination"] == "verified.csv"


def test_rejection_flow_and_rejection_reasons(api_client, accounts):
    owner = accounts("rej-owner@example.com")
    workspace = owner.post("/api/v1/workspaces", json={"name": "Rejection WS"}).json()["id"]

    req = owner.post(
        "/api/v1/tools/execute",
        json={
            "workspace_id": workspace,
            "tool_name": "code.apply_patch",
            "parameters": {"files": {"config.py": "DEBUG = True"}},
        },
    )
    approval_id = req.json()["approval_id"]

    # Reject approval
    rej_resp = owner.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={
            "decision": "rejected",
            "reason": "Security policy violation: debug mode disabled in production",
        },
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"] == "rejected"

    # Execution is refused
    exec_resp = owner.post(
        "/api/v1/tools/execute",
        json={
            "workspace_id": workspace,
            "tool_name": "code.apply_patch",
            "parameters": {"files": {"config.py": "DEBUG = True"}},
            "approval_id": approval_id,
        },
    )
    assert exec_resp.status_code == 403
    assert "rejected" in exec_resp.text


def test_rbac_and_workspace_tenant_isolation(api_client, accounts):
    owner_a = accounts("iso-owner-a@example.com")
    owner_b = accounts("iso-owner-b@example.com")
    viewer_a = accounts("iso-viewer-a@example.com")

    ws_a = owner_a.post("/api/v1/workspaces", json={"name": "WS Alpha"}).json()["id"]
    ws_b = owner_b.post("/api/v1/workspaces", json={"name": "WS Beta"}).json()["id"]
    assert owner_b.get(f"/api/v1/approvals?workspace_id={ws_b}").json() == []
    owner_a.post(
        f"/api/v1/workspaces/{ws_a}/members",
        json={"email": "iso-viewer-a@example.com", "role": "viewer"},
    )

    req_a = owner_a.post(
        "/api/v1/tools/execute",
        json={
            "workspace_id": ws_a,
            "tool_name": "file.export_csv",
            "parameters": {"filename": "alpha.csv", "csv_content": "1,2,3"},
        },
    )
    approval_a_id = req_a.json()["approval_id"]

    # 1. Viewer cannot decide approvals (403)
    viewer_decision = viewer_a.post(
        f"/api/v1/approvals/{approval_a_id}/decision",
        json={"decision": "approved"},
    )
    assert viewer_decision.status_code == 403

    # 2. Tenant B cannot see Tenant A's approval (404/403)
    cross_tenant_get = owner_b.get(f"/api/v1/approvals/{approval_a_id}")
    assert cross_tenant_get.status_code in (403, 404)

    # 3. Tenant B cannot decide Tenant A's approval (404/403)
    cross_tenant_decision = owner_b.post(
        f"/api/v1/approvals/{approval_a_id}/decision",
        json={"decision": "approved"},
    )
    assert cross_tenant_decision.status_code in (403, 404)
