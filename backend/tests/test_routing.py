from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from app import main as main_module
from app import planner, planning, planning_runtime, worker
from app.heartbeats import record_heartbeat
from app.models import Event, Task, TaskAttempt, Workflow, now
from app.planning_runtime import routing_profiles
from app.registry import AgentRegistry, build_registry
from app.routing import RoutingError, RoutingHistory, select_route


def test_fixed_and_adaptive_baselines_are_deterministic_and_explain_scores():
    builtins = build_registry()
    fixed_agent = builtins.select("csv.inspect")
    adaptive_agent = replace(fixed_agent, agent_id="csv_inspector_adaptive")
    tool_agent = replace(fixed_agent, agent_id="csv_inspector_tool", allowed_tools=("web.fetch",))
    disabled_agent = replace(fixed_agent, agent_id="csv_inspector_disabled", enabled=False)
    wrong_input_agent = replace(
        fixed_agent,
        agent_id="csv_inspector_wrong_input",
        required_context=(("summary", "csv.summarize"),),
    )
    unrelated = builtins.select("csv.summarize")
    registry = AgentRegistry(
        [fixed_agent, adaptive_agent, tool_agent, disabled_agent, wrong_input_agent, unrelated]
    )
    histories = {
        fixed_agent.agent_id: RoutingHistory(
            completed=10,
            succeeded=5,
            average_latency_ms=5000,
            average_cost_usd=Decimal("0.01"),
        ),
        adaptive_agent.agent_id: RoutingHistory(
            completed=10,
            succeeded=9,
            average_latency_ms=500,
            average_cost_usd=Decimal("0.001"),
        ),
    }
    workers = {fixed_agent.agent_id: 1, adaptive_agent.agent_id: 3, tool_agent.agent_id: 3}
    arguments = {
        "capability": "csv.inspect",
        "schema_version": "1.0",
        "agent_version": "1.0.0",
        "required_context": {},
        "registry": registry,
        "histories": histories,
        "worker_counts": workers,
    }
    fixed, fixed_decision = select_route(
        mode="fixed", requested_agent=fixed_agent.agent_id, **arguments
    )
    adaptive, adaptive_decision = select_route(
        mode="adaptive", requested_agent=fixed_agent.agent_id, **arguments
    )
    repeated, repeated_decision = select_route(
        mode="adaptive", requested_agent=fixed_agent.agent_id, **arguments
    )

    assert fixed.agent_id == fixed_agent.agent_id
    assert adaptive.agent_id == adaptive_agent.agent_id
    assert repeated.agent_id == adaptive.agent_id
    assert adaptive_decision == repeated_decision
    assert adaptive_decision["weights"] == {
        "reliability": 0.45,
        "latency": 0.25,
        "availability": 0.20,
        "cost": 0.10,
    }
    candidates = {row["agent"]: row for row in adaptive_decision["candidates"]}
    assert candidates[tool_agent.agent_id]["reason"] == "tool_permission_denied"
    assert candidates[disabled_agent.agent_id]["reason"] == "agent_disabled"
    assert candidates[wrong_input_agent.agent_id]["reason"] == "input_contract_mismatch"
    assert candidates[unrelated.agent_id]["reason"] == "capability_mismatch"
    assert adaptive_decision["score"] == 0.893182
    assert (
        candidates[adaptive_agent.agent_id]["observations"]["average_reported_cost_usd"] == "0.001"
    )
    assert fixed_decision["selected_agent"] == fixed_agent.agent_id


def test_adaptive_routing_requires_an_available_compatible_worker():
    agent = build_registry().select("csv.inspect")
    with pytest.raises(RoutingError, match="No registered agent") as error:
        select_route(
            mode="adaptive",
            requested_agent=agent.agent_id,
            capability=agent.capability,
            schema_version=agent.schema_version,
            agent_version=agent.version,
            required_context={},
            registry=AgentRegistry([agent]),
            worker_counts={agent.agent_id: 0},
        )
    assert error.value.code == "no_eligible_agent"


def test_adaptive_decision_is_persisted_in_plan_task_and_event(accounts, migrated_database):
    client = accounts("routing@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "Routing"}).json()["id"]
    _, sessions, _ = migrated_database
    with sessions.begin() as db:
        record_heartbeat(db)

    response = client.post(
        "/api/v1/workflows",
        json={
            "workspace_id": workspace,
            "prompt": "Analyze these rows",
            "csv_text": "item,sales\nA,10\nB,20",
            "routing_mode": "adaptive",
        },
    )
    assert response.status_code == 202, response.text
    workflow_id = response.json()["workflow_id"]
    workflow = client.get(f"/api/v1/workflows/{workflow_id}").json()

    assert workflow["routing_mode"] == "adaptive"
    assert workflow["plan"]["tasks"]
    assert all(task["routing_decision"]["mode"] == "adaptive" for task in workflow["tasks"])
    assert all(
        task["routing_decision"]["selected_agent"] == task["agent"] for task in workflow["tasks"]
    )
    planned_agents = {task["id"]: task["agent"] for task in workflow["plan"]["tasks"]}
    assert all(planned_agents[task["name"]] == task["agent"] for task in workflow["tasks"])
    with sessions() as db:
        decisions = db.query(Event).filter_by(workflow_id=workflow_id, kind="route_selected").all()
        assert len(decisions) == 3
        assert all(event.details["mode"] == "adaptive" for event in decisions)
    for _ in range(4):
        worker.tick()
    assert client.get(f"/api/v1/workflows/{workflow_id}").json()["status"] == "succeeded"


def test_adaptive_workflow_is_not_created_without_compatible_workers(accounts, migrated_database):
    client = accounts("routing-offline@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "No workers"}).json()["id"]
    _, sessions, _ = migrated_database
    response = client.post(
        "/api/v1/workflows",
        json={
            "workspace_id": workspace,
            "prompt": "Wait for a worker",
            "csv_text": "item,sales\nA,10",
            "routing_mode": "adaptive",
        },
    )
    assert response.status_code == 503
    with sessions() as db:
        assert db.query(Event).filter_by(kind="route_selected").count() == 0


def test_routing_history_is_scoped_to_the_workflow_workspace(accounts, migrated_database):
    client = accounts("routing-scope@example.com")
    first_workspace = client.post("/api/v1/workspaces", json={"name": "First"}).json()["id"]
    second_workspace = client.post("/api/v1/workspaces", json={"name": "Second"}).json()["id"]
    _, sessions, _ = migrated_database
    started = now() - timedelta(seconds=4)
    with sessions.begin() as db:
        for index, workspace_id in enumerate((first_workspace, second_workspace)):
            workflow = Workflow(workspace_id=workspace_id, prompt=f"Run {index}")
            db.add(workflow)
            db.flush()
            task = Task(
                workflow_id=workflow.id,
                name=f"inspect_{index}",
                agent="csv_inspector",
                capability="csv.inspect",
                status="succeeded",
                dependencies=[],
                input_bindings={},
                ordinal=0,
            )
            db.add(task)
            db.flush()
            db.add(
                TaskAttempt(
                    task_id=task.id,
                    workflow_id=workflow.id,
                    attempt_number=1,
                    idempotency_key=f"scope:{index}",
                    worker_id=f"worker:{index}",
                    status="succeeded",
                    lease_expires_at=started,
                    request_id=f"request:{index}",
                    result_envelope={"usage": {"cost_usd": index + 0.25}},
                    started_at=started,
                    finished_at=now(),
                )
            )
    with sessions() as db:
        histories, _ = routing_profiles(db, first_workspace, {"csv.inspect"}, build_registry())
    assert histories["csv_inspector"].completed == 1
    assert histories["csv_inspector"].average_cost_usd == Decimal("0.25")


def test_adaptive_baseline_routes_to_the_higher_scoring_registered_agent(
    accounts, migrated_database, monkeypatch
):
    client = accounts("adaptive-choice@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "Adaptive choice"}).json()["id"]
    _, sessions, _ = migrated_database
    base_registry = build_registry()
    requested = base_registry.select("csv.inspect")
    alternative = replace(requested, agent_id="csv_inspector_adaptive")
    registry = AgentRegistry([*base_registry.all(), alternative])
    original_add_tasks = planning_runtime.add_tasks
    monkeypatch.setattr(planner, "REGISTRY", registry)
    monkeypatch.setattr(planning, "REGISTRY", registry)
    monkeypatch.setattr(worker, "REGISTRY", registry)
    monkeypatch.setattr(
        main_module,
        "add_tasks",
        lambda db, workflow, proposal: original_add_tasks(db, workflow, proposal, registry),
    )
    started = now() - timedelta(milliseconds=25)
    with sessions.begin() as db:
        record_heartbeat(db, registry=registry, worker_id="adaptive-worker")
        history = Workflow(workspace_id=workspace, prompt="Prior attempt", status="succeeded")
        db.add(history)
        db.flush()
        task = Task(
            workflow_id=history.id,
            name="prior_inspection",
            agent=alternative.agent_id,
            capability=alternative.capability,
            status="succeeded",
            dependencies=[],
            input_bindings={},
            ordinal=0,
        )
        db.add(task)
        db.flush()
        db.add(
            TaskAttempt(
                task_id=task.id,
                workflow_id=history.id,
                attempt_number=1,
                idempotency_key="prior-adaptive-attempt",
                worker_id="adaptive-worker",
                status="succeeded",
                lease_expires_at=started,
                request_id="prior-adaptive-request",
                result_envelope={"usage": {"cost_usd": 0}},
                started_at=started,
                finished_at=now(),
            )
        )

    response = client.post(
        "/api/v1/workflows",
        json={
            "workspace_id": workspace,
            "prompt": "Choose an agent by history",
            "csv_text": "item,sales\nA,10",
            "routing_mode": "adaptive",
        },
    )
    assert response.status_code == 202, response.text
    workflow_id = response.json()["workflow_id"]
    details = client.get(f"/api/v1/workflows/{workflow_id}").json()
    inspection = next(task for task in details["tasks"] if task["capability"] == "csv.inspect")
    assert inspection["agent"] == alternative.agent_id
    assert inspection["routing_decision"]["requested_agent"] == requested.agent_id
    assert inspection["routing_decision"]["selected_agent"] == alternative.agent_id
    for _ in range(4):
        worker.tick("adaptive-worker")
    details = client.get(f"/api/v1/workflows/{workflow_id}").json()
    assert details["status"] == "succeeded"
    assert any(task["execution"]["agent_type"] == alternative.agent_id for task in details["tasks"])
