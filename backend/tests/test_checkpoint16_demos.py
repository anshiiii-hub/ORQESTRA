"""The guided scenario launches create persistent, workspace-scoped walkthrough records."""

import pytest
from sqlalchemy import select

from app.models import Approval, Artifact, Task, Workflow


@pytest.mark.parametrize(
    ("demo_key", "expected_status"),
    [
        ("demo_a_research", "succeeded"),
        ("demo_b_coding", "waiting_approval"),
        ("demo_c_dataset", "succeeded"),
        ("demo_d_resilience", "paused"),
    ],
)
def test_demo_launch_persists_scenario_and_outputs(
    api_client, accounts, migrated_database, demo_key, expected_status
):
    _, sessions, _ = migrated_database
    owner = accounts(f"{demo_key}@example.com")
    workspace_id = owner.post("/api/v1/workspaces", json={"name": f"{demo_key} workspace"}).json()[
        "id"
    ]

    catalog = owner.get("/api/v1/demos/catalog")
    assert catalog.status_code == 200
    assert demo_key in {item["key"] for item in catalog.json()["catalog"]}

    response = owner.post(f"/api/v1/demos/{demo_key}/launch", json={"workspace_id": workspace_id})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == expected_status

    with sessions() as db:
        workflow = db.get(Workflow, payload["workflow_id"])
        assert workflow is not None
        assert workflow.workspace_id == workspace_id
        assert workflow.status == expected_status
        tasks = list(
            db.scalars(select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal))
        )
        assert tasks
        if demo_key == "demo_a_research":
            artifacts = list(
                db.scalars(select(Artifact).where(Artifact.workflow_id == workflow.id)).all()
            )
            assert artifacts
            assert "not live research" in artifacts[0].content_text.lower()
        elif demo_key in {"demo_b_coding", "demo_c_dataset"}:
            approval = db.get(Approval, payload["approval_id"])
            assert approval is not None
            assert approval.task_id in {task.id for task in tasks}
        if demo_key == "demo_c_dataset":
            artifacts = list(
                db.scalars(select(Artifact).where(Artifact.workflow_id == workflow.id)).all()
            )
            assert any(item.mime_type == "image/svg+xml" for item in artifacts)
            assert all(item.task_id in {task.id for task in tasks} for item in artifacts)


def test_demo_launch_rejects_cross_workspace_user(api_client, accounts):
    owner_a = accounts("demo-owner-a@example.com")
    owner_b = accounts("demo-owner-b@example.com")
    workspace_a = owner_a.post("/api/v1/workspaces", json={"name": "Demo A"}).json()["id"]
    workspace_b = owner_b.post("/api/v1/workspaces", json={"name": "Demo B"}).json()["id"]
    assert (
        owner_b.post(
            "/api/v1/demos/demo_c_dataset/launch", json={"workspace_id": workspace_a}
        ).status_code
        == 404
    )
    assert (
        owner_b.post(
            "/api/v1/demos/demo_c_dataset/launch", json={"workspace_id": workspace_b}
        ).status_code
        == 200
    )
