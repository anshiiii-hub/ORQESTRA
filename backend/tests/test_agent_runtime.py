from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import planner, worker
from app.contracts import AgentRequest, AgentResult
from app.heartbeats import HEARTBEAT_TTL, record_heartbeat
from app.models import Event, Task, TaskAttempt, WorkerHeartbeat, Workflow, now
from app.registry import AgentRegistry, build_registry


@pytest.fixture
def runtime(accounts, migrated_database):
    client = accounts("runtime@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "Runtime tests"}).json()["id"]
    created = client.post(
        "/api/v1/workflows",
        json={
            "workspace_id": workspace,
            "prompt": "Analyze sales",
            "csv_text": "item,sales\nA,10\nB,20",
        },
    )
    assert created.status_code == 202
    _, sessions, _ = migrated_database
    return client, workspace, created.json()["workflow_id"], sessions


def tasks_for(db, wid):
    return db.scalars(select(Task).where(Task.workflow_id == wid).order_by(Task.ordinal)).all()


def test_worker_persists_correlated_contracts(runtime):
    client, _, wid, sessions = runtime
    for _ in range(4):
        worker.tick()
    with sessions() as db:
        tasks = tasks_for(db, wid)
        request_ids = set()
        for task in tasks:
            request = AgentRequest.model_validate(task.request_envelope)
            result = AgentResult.model_validate(task.result_envelope)
            assert result.status == "succeeded"
            assert result.request_id == request.request_id
            assert result.task_id == task.id
            assert task.result == result.structured_output
            request_ids.add(request.request_id)
        assert len(request_ids) == 3
        attempt_rows = db.scalars(select(TaskAttempt).where(TaskAttempt.workflow_id == wid)).all()
        assert len(attempt_rows) == 3
        assert all(attempt.status == "succeeded" for attempt in attempt_rows)
        assert len({attempt.idempotency_key for attempt in attempt_rows}) == 3
        assert all(attempt.request_envelope for attempt in attempt_rows)
        assert set(tasks[-1].request_envelope["context_refs"].values()) == {
            tasks[0].id,
            tasks[1].id,
        }
    response = client.get(f"/api/v1/workflows/{wid}").json()
    assert response["status"] == "succeeded"
    assert response["tasks"][0]["capability"] == "csv.inspect"
    assert "request_envelope" not in response["tasks"][0]
    assert response["tasks"][0]["execution"]["schema_version"] == "1.0"
    assert response["tasks"][0]["execution_attempts"][0]["status"] == "succeeded"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("agent", "unregistered", "unknown_agent"),
        ("capability", "csv.summarize", "capability_mismatch"),
        ("schema_version", "2.0", "incompatible_version"),
        ("agent_version", "2.0.0", "incompatible_version"),
    ],
)
def test_worker_rejects_persisted_incompatible_tasks(runtime, field, value, code):
    _, _, wid, sessions = runtime
    with sessions.begin() as db:
        setattr(tasks_for(db, wid)[0], field, value)
    assert worker.tick()
    with sessions() as db:
        tasks = tasks_for(db, wid)
        assert tasks[0].error_code == code
        assert tasks[0].result is None
        assert all(task.status == "skipped" and task.attempts == 0 for task in tasks[1:])
        assert db.get(Workflow, wid).status == "failed"


def test_disabled_agent_rejected_before_execution(runtime, monkeypatch):
    _, _, wid, sessions = runtime
    monkeypatch.setattr(worker, "REGISTRY", build_registry(("csv_inspector",)))
    worker.tick()
    with sessions() as db:
        assert tasks_for(db, wid)[0].error_code == "disabled_agent"
        assert db.get(Workflow, wid).status == "failed"


def test_unavailable_plan_returns_503_without_creating_workflow(runtime, monkeypatch):
    client, workspace, _, sessions = runtime
    monkeypatch.setattr(planner, "REGISTRY", build_registry(("csv_inspector",)))
    response = client.post(
        "/api/v1/workflows",
        json={"workspace_id": workspace, "prompt": "Denied plan", "csv_text": "a\n1"},
    )
    assert response.status_code == 503
    with sessions() as db:
        assert len(db.scalars(select(Workflow)).all()) == 1


def test_invalid_result_is_not_published_or_used_by_children(runtime, monkeypatch):
    client, _, wid, sessions = runtime
    definitions = build_registry().all()
    registry = AgentRegistry(
        [
            replace(agent, handler=lambda *_: {"secret": "must-not-leak"})
            if agent.agent_id == "csv_inspector"
            else agent
            for agent in definitions
        ]
    )
    monkeypatch.setattr(worker, "REGISTRY", registry)
    worker.tick()
    response = client.get(f"/api/v1/workflows/{wid}")
    assert response.json()["status"] == "failed"
    assert "must-not-leak" not in response.text
    with sessions() as db:
        tasks = tasks_for(db, wid)
        assert tasks[0].result is None and tasks[0].error_code == "invalid_result"
        assert tasks[0].result_envelope["status"] == "failed"
        assert all(task.attempts == 0 for task in tasks[1:])
        events = db.scalars(select(Event).where(Event.workflow_id == wid)).all()
        assert events[-1].kind == "workflow_failed"
        assert all("must-not-leak" not in str(event.details) for event in events)


def test_invalid_parent_output_is_revalidated(runtime):
    _, _, wid, sessions = runtime
    worker.tick()
    with sessions.begin() as db:
        tasks_for(db, wid)[0].result = {"row_count": "bad"}
    worker.tick()
    with sessions() as db:
        tasks = tasks_for(db, wid)
        assert tasks[1].error_code == "invalid_input"
        assert tasks[2].status == "skipped"


def test_input_binding_cannot_read_an_undeclared_parent(runtime):
    _, _, wid, sessions = runtime
    worker.tick()
    with sessions.begin() as db:
        tasks_for(db, wid)[1].input_bindings = {"inspect": "verify"}
    worker.tick()
    with sessions() as db:
        assert tasks_for(db, wid)[1].error_code == "invalid_input"


def test_cancellation_during_execution_discards_result(runtime, monkeypatch):
    client, _, wid, sessions = runtime

    def original_with_cancellation(csv_text, context):
        assert client.post(f"/api/v1/workflows/{wid}/cancel").status_code == 200
        return build_registry().select("csv.inspect").handler(csv_text, context)

    definitions = build_registry().all()
    monkeypatch.setattr(
        worker,
        "REGISTRY",
        AgentRegistry(
            [
                replace(agent, handler=original_with_cancellation)
                if agent.agent_id == "csv_inspector"
                else agent
                for agent in definitions
            ]
        ),
    )
    worker.tick()
    with sessions() as db:
        task = tasks_for(db, wid)[0]
        assert task.status == "cancelled" and task.result is None and task.result_envelope is None
        assert db.get(Workflow, wid).status == "cancelled"


def test_agent_catalog_requires_login_and_uses_compatible_live_heartbeats(runtime, api_client):
    client, _, _, sessions = runtime
    assert api_client.get("/api/v1/agents").status_code == 401
    assert api_client.get("/api/v1/agents/contracts").status_code == 401
    assert all(agent["availability"] == "offline" for agent in client.get("/api/v1/agents").json())
    with sessions.begin() as db:
        record_heartbeat(db, worker_id="worker-a")
    catalog = client.get("/api/v1/agents").json()
    assert len(catalog) == 3
    assert all(
        agent["availability"] == "ready" and agent["available_workers"] == 1 for agent in catalog
    )
    assert all(agent["allowed_tools"] == [] for agent in catalog)
    with sessions.begin() as db:
        db.get(WorkerHeartbeat, "worker-a").seen_at = now() - timedelta(seconds=HEARTBEAT_TTL + 1)
    assert all(agent["availability"] == "offline" for agent in client.get("/api/v1/agents").json())
    with sessions.begin() as db:
        heartbeat = db.get(WorkerHeartbeat, "worker-a")
        heartbeat.seen_at = now()
        heartbeat.agent_versions = {
            "csv_inspector": {"agent_version": "9.0.0", "schema_version": "1.0"}
        }
    assert all(agent["availability"] == "offline" for agent in client.get("/api/v1/agents").json())
    schemas = client.get("/api/v1/agents/contracts").json()
    assert schemas["agent-request.v1.json"]["properties"]["schema_version"]["const"] == "1.0"


def test_disabled_agents_are_not_advertised_by_heartbeat(runtime):
    from app.heartbeats import agent_catalog

    _, _, _, sessions = runtime
    registry = build_registry(("csv_inspector",))
    with sessions.begin() as db:
        record_heartbeat(db, registry, worker_id="worker-b")
    with sessions() as db:
        catalog = agent_catalog(db, registry)
        inspector = next(agent for agent in catalog if agent["id"] == "csv_inspector")
        assert inspector["availability"] == "disabled" and inspector["available_workers"] == 0
        assert "csv_inspector" not in db.get(WorkerHeartbeat, "worker-b").agent_versions
