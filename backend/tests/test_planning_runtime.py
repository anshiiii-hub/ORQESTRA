import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app import planning_runtime, worker
from app.llm import Completion, PlanningError
from app.models import Event, Task, Workflow, now
from app.planner import demo_plan
from app.planning import PlannerProposal, planning_messages


@pytest.fixture
def configured(monkeypatch):
    for name, value in {
        "LLM_PLANNER_ENABLED": "true",
        "LLM_MODEL": "test-model",
        "LLM_INPUT_PRICE_PER_MILLION": "1",
        "LLM_OUTPUT_PRICE_PER_MILLION": "2",
        "OPENAI_API_KEY": "server-only-test-secret",
    }.items():
        monkeypatch.setenv(name, value)


def proposal(variant="valid"):
    body = {
        "schema_version": "1.0",
        "tasks": demo_plan().model_dump()["tasks"],
        "final_task_id": "verify",
    }
    if variant == "bad_json":
        return "Not a JSON object"
    if variant == "renamed":
        mapping = {"inspect": "node-1", "summarize": "node-2", "verify": "node-3"}
        body["final_task_id"] = "node-3"
        for task in body["tasks"]:
            task["id"] = mapping[task["id"]]
            task["dependencies"] = [mapping[parent] for parent in task["dependencies"]]
            task["input_bindings"] = {
                alias: mapping[parent] for alias, parent in task["input_bindings"].items()
            }
    if variant == "reversed":
        body["tasks"].reverse()
    if variant == "unauthorized":
        body["tasks"][0]["tool_permissions"] = ["shell.exec"]
    if variant == "unknown_agent":
        body["tasks"][0]["agent"] = "web_researcher"
    if variant == "missing_dependency":
        body["tasks"][0]["dependencies"] = ["foreign-task"]
    if variant == "cycle":
        body["tasks"][0]["dependencies"] = ["verify"]
    if variant == "unverified":
        body["final_task_id"] = "inspect"
    if variant == "future_version":
        body["schema_version"] = "2.0"
    if variant == "unrelated":
        body["tasks"].append(
            {"id": "unused", "agent": "csv_inspector", "capability": "csv.inspect"}
        )
    return json.dumps(body)


class ReplayProvider:
    def __init__(self, replies, sessions, on_call=None):
        self.replies = iter(replies)
        self.calls = []
        self.sessions = sessions
        self.on_call = on_call

    def complete(self, messages, max_output_tokens, timeout_seconds, input_token_bound):
        with self.sessions() as db:
            workflow = db.scalar(select(Workflow).where(Workflow.status == "planning"))
            assert workflow is not None
            state = workflow.planning
            assert state["attempts"][-1]["status"] == "reserved"
            assert state["accounted_tokens"] <= state["max_tokens"]
            assert Decimal(state["accounted_cost_usd"]) <= Decimal(state["max_cost_usd"])
            assert "server-only-test-secret" not in json.dumps(state)
        assert 0 < timeout_seconds <= 30
        assert 256 <= max_output_tokens <= 2048
        assert "PRIVATE_ROW_VALUE" not in json.dumps(messages)
        self.calls.append(deepcopy(messages))
        if self.on_call:
            self.on_call()
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply if isinstance(reply, Completion) else Completion(reply, 100, 50)


@pytest.fixture
def runtime(configured, accounts, migrated_database, monkeypatch):
    client = accounts("planner@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "Planning tests"}).json()["id"]
    _, sessions, _ = migrated_database
    providers = []

    def install(replies, on_call=None):
        provider = ReplayProvider(replies, sessions, on_call)
        providers.append(provider)
        monkeypatch.setattr(planning_runtime, "make_provider", lambda _: provider)
        return provider

    def create(**updates):
        body = {
            "workspace_id": workspace,
            "prompt": "Analyze and verify sales",
            "csv_text": "item,sales\nPRIVATE_ROW_VALUE,10\nB,20",
            "planner_mode": "llm",
        }
        response = client.post("/api/v1/workflows", json={**body, **updates})
        assert response.status_code == 202, response.text
        return response.json()["workflow_id"]

    return client, workspace, sessions, install, create


def read(sessions, wid):
    with sessions() as db:
        return db.get(Workflow, wid)


def test_llm_plan_is_asynchronous_and_produces_verified_output(runtime):
    client, _, sessions, install, create = runtime
    provider = install([proposal("reversed")])
    wid = create()
    assert provider.calls == []
    assert client.get(f"/api/v1/workflows/{wid}").json()["tasks"] == []
    worker.tick()
    workflow = read(sessions, wid)
    assert workflow.status == "queued" and workflow.planning["status"] == "succeeded"
    assert len(provider.calls) == 1
    assert workflow.planning["accounted_tokens"] == 150
    assert Decimal(workflow.planning["accounted_cost_usd"]) == Decimal("0.0002")
    for _ in range(4):
        worker.tick()
    response = client.get(f"/api/v1/workflows/{wid}")
    assert response.json()["status"] == "succeeded"
    assert response.json()["output"]["summary"]["numeric_columns"]["sales"]["sum"] == 30
    assert "server-only-test-secret" not in response.text
    assert response.json()["plan"]["final_task_id"] == "verify"


def test_one_bounded_repair_is_recorded_and_accounted(runtime):
    _, _, sessions, install, create = runtime
    provider = install(["not JSON", proposal()])
    wid = create()
    worker.tick()
    state = read(sessions, wid).planning
    assert state["status"] == "succeeded" and len(provider.calls) == 2
    assert len(state["attempts"]) == 2
    assert state["attempts"][0]["error_code"] == "planning_invalid_plan"
    assert state["accounted_tokens"] == 300
    assert Decimal(state["accounted_cost_usd"]) == Decimal("0.0004")
    assert len(provider.calls[0]) == 2 and len(provider.calls[1]) == 3


def test_invalid_plans_never_fall_back_or_call_more_than_twice(runtime):
    _, _, sessions, install, create = runtime
    provider = install(["bad", "still bad", proposal()])
    wid = create()
    worker.tick()
    workflow = read(sessions, wid)
    assert (
        workflow.status == "failed" and workflow.planning["error_code"] == "planning_invalid_plan"
    )
    assert workflow.execution_plan is None
    assert len(provider.calls) == 2 and worker.tick() is False
    with sessions() as db:
        assert db.scalars(select(Task)).all() == []


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"planning_max_tokens": 512}, "planning_token_budget"),
        ({"planning_max_cost_usd": 0.000001}, "planning_cost_budget"),
    ],
)
def test_insufficient_budget_prevents_network_call(runtime, overrides, code):
    _, _, sessions, install, create = runtime
    provider = install([proposal()])
    wid = create(**overrides)
    worker.tick()
    assert provider.calls == []
    workflow = read(sessions, wid)
    assert workflow.planning["error_code"] == code
    assert workflow.planning["attempts"] == []


def test_expired_deadline_or_changed_configuration_prevents_call(runtime, monkeypatch):
    _, _, sessions, install, create = runtime
    provider = install([proposal()])
    wid = create()
    with sessions.begin() as db:
        workflow = db.get(Workflow, wid)
        state = deepcopy(workflow.planning)
        state["deadline_at"] = (now() - timedelta(seconds=1)).isoformat()
        workflow.planning = state
    worker.tick()
    assert read(sessions, wid).planning["error_code"] == "planning_deadline"
    assert provider.calls == []
    wid = create()
    monkeypatch.setenv("LLM_MODEL", "different-model")
    worker.tick()
    assert read(sessions, wid).planning["error_code"] == "planning_configuration_changed"
    assert provider.calls == []


def test_provider_timeout_retains_reservation_and_does_not_retry(runtime):
    _, _, sessions, install, create = runtime
    provider = install([PlanningError("provider_timeout", "Planning provider timed out")])
    wid = create()
    worker.tick()
    state = read(sessions, wid).planning
    assert state["status"] == "failed" and len(provider.calls) == 1
    assert state["attempts"][0]["status"] == "indeterminate"
    assert state["accounted_tokens"] > 150 and Decimal(state["accounted_cost_usd"]) > 0


def test_worker_crash_does_not_replay_potentially_billable_call(runtime):
    _, _, sessions, install, create = runtime
    provider = install([KeyboardInterrupt()])
    wid = create()
    with pytest.raises(KeyboardInterrupt):
        worker.tick()
    assert read(sessions, wid).status == "planning"
    planning_runtime.recover_planning(sessions)
    state = read(sessions, wid).planning
    assert state["error_code"] == "planning_interrupted"
    assert state["attempts"][0]["status"] == "indeterminate"
    assert worker.tick() is False and len(provider.calls) == 1


def test_crash_before_reservation_can_requeue_without_charge(runtime):
    _, _, sessions, install, create = runtime
    provider = install([proposal()])
    wid = create()
    assert planning_runtime.claim_planning(sessions) == wid
    planning_runtime.recover_planning(sessions)
    assert read(sessions, wid).status == "queued"
    worker.tick()
    assert read(sessions, wid).planning["status"] == "succeeded"
    assert len(provider.calls) == 1


def test_cancellation_in_flight_accounts_usage_but_does_not_publish_plan(runtime):
    client, _, sessions, install, create = runtime
    wid = create()
    provider = install(
        [proposal()], lambda: client.post(f"/api/v1/workflows/{wid}/cancel").raise_for_status()
    )
    worker.tick()
    workflow = read(sessions, wid)
    assert workflow.status == "cancelled" and workflow.execution_plan is None
    assert len(provider.calls) == 1
    assert workflow.planning["attempts"][0]["status"] == "completed"
    assert Decimal(workflow.planning["accounted_cost_usd"]) == Decimal("0.0002")


def test_offline_default_never_uses_provider_even_when_enabled(runtime):
    client, _, sessions, install, create = runtime
    provider = install([RuntimeError("must not call")])
    wid = create(planner_mode="offline")
    for _ in range(4):
        worker.tick()
    workflow = read(sessions, wid)
    assert workflow.status == "succeeded"
    assert workflow.planning["provider"] == "offline" and workflow.planning["attempts"] == []
    assert provider.calls == []
    assert client.get(f"/api/v1/workflows/{wid}/plan").status_code == 200


def test_unconfigured_provider_and_secret_free_configuration(runtime, monkeypatch):
    client, workspace, _, _, _ = runtime
    response = client.get("/api/v1/planner/config")
    assert response.json()["llm_available"] is True
    assert "server-only-test-secret" not in response.text
    monkeypatch.setenv("LLM_PLANNER_ENABLED", "false")
    assert client.get("/api/v1/planner/config").json()["llm_available"] is False
    assert (
        client.post(
            "/api/v1/workflows",
            json={
                "workspace_id": workspace,
                "prompt": "Analyze",
                "csv_text": "a\n1",
                "planner_mode": "llm",
            },
        ).status_code
        == 503
    )


GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "planning_golden.json").read_text())


@pytest.mark.parametrize(
    "case", GOLDEN, ids=[case["variant"] + "-" + str(i) for i, case in enumerate(GOLDEN)]
)
def test_twenty_golden_replays_yield_valid_dags_or_explicit_errors(runtime, case):
    _, _, sessions, install, create = runtime
    content = proposal(case["variant"])
    provider = install([content, content])
    wid = create(prompt=case["prompt"])
    worker.tick()
    workflow = read(sessions, wid)
    if case["variant"] in ("valid", "renamed", "reversed"):
        assert workflow.planning["status"] == "succeeded" and len(provider.calls) == 1
        assert PlannerProposal.model_validate(workflow.execution_plan)
    else:
        assert (
            workflow.status == "failed"
            and workflow.planning["error_code"] == "planning_invalid_plan"
        )
        assert len(provider.calls) == 2 and workflow.execution_plan is None


def test_cancelled_interrupted_call_is_not_replayed(runtime):
    client, _, sessions, install, create = runtime
    wid = create()

    def cancel():
        client.post(f"/api/v1/workflows/{wid}/cancel").raise_for_status()

    provider = install([KeyboardInterrupt()], cancel)
    with pytest.raises(KeyboardInterrupt):
        worker.tick()
    planning_runtime.recover_planning(sessions)
    workflow = read(sessions, wid)
    assert workflow.status == "cancelled"
    assert workflow.planning["attempts"][0]["status"] == "indeterminate"
    assert worker.tick() is False and len(provider.calls) == 1


def test_late_provider_response_is_accounted_but_cannot_publish_plan(runtime, monkeypatch):
    _, _, sessions, install, create = runtime
    wid = create()
    future = now() + timedelta(minutes=10)
    install([proposal()], lambda: monkeypatch.setattr(planning_runtime, "now", lambda: future))
    worker.tick()
    workflow = read(sessions, wid)
    assert workflow.status == "failed" and workflow.execution_plan is None
    assert workflow.planning["error_code"] == "planning_deadline"
    assert Decimal(workflow.planning["accounted_cost_usd"]) == Decimal("0.0002")


@pytest.mark.parametrize("budget", ["tokens", "cost"])
def test_repair_must_fit_remaining_budget(runtime, monkeypatch, budget):
    from app.llm import planner_settings
    from app.planning import input_token_bound

    _, _, sessions, _, create = runtime
    messages = planning_messages(
        "Analyze and verify sales", "item,sales\nPRIVATE_ROW_VALUE,10\nB,20"
    )
    bound = input_token_bound(messages)
    calls = []

    class ExhaustBudget:
        def complete(self, messages, max_output_tokens, timeout_seconds, input_token_bound):
            calls.append(messages)
            return Completion("bad JSON", input_token_bound, max_output_tokens)

    monkeypatch.setattr(planning_runtime, "make_provider", lambda _: ExhaustBudget())
    overrides = (
        {"planning_max_tokens": bound + 2048}
        if budget == "tokens"
        else {"planning_max_cost_usd": float(planner_settings().cost(bound, 2048))}
    )
    wid = create(**overrides)
    worker.tick()
    state = read(sessions, wid).planning
    assert state["error_code"] == f"planning_{'token' if budget == 'tokens' else 'cost'}_budget"
    assert len(calls) == 1 and len(state["attempts"]) == 1


def test_truncated_response_gets_only_one_repair(runtime):
    _, _, sessions, install, create = runtime
    provider = install([Completion(proposal(), 100, 50, truncated=True), proposal()])
    wid = create()
    worker.tick()
    assert len(provider.calls) == 2 and read(sessions, wid).planning["status"] == "succeeded"


def test_unexpected_failure_never_exposes_exception_or_credentials(runtime):
    client, _, sessions, install, create = runtime
    install([RuntimeError("server-only-test-secret /private/host-file")])
    wid = create()
    worker.tick()
    response = client.get(f"/api/v1/workflows/{wid}")
    assert response.json()["planning"]["error_code"] == "planning_failed"
    assert (
        "server-only-test-secret" not in response.text and "/private/host-file" not in response.text
    )
    with sessions() as db:
        assert all(
            "server-only-test-secret" not in str(event.details)
            for event in db.scalars(select(Event))
        )


def test_plans_are_private_and_viewers_cannot_start_paid_planning(runtime, accounts, api_client):
    client, workspace, _, install, create = runtime
    outsider = accounts("outside@example.com")
    install([proposal()])
    wid = create()
    assert api_client.get("/api/v1/planner/config").status_code == 401
    assert outsider.get(f"/api/v1/workflows/{wid}/plan").status_code == 404
    member = client.post(
        f"/api/v1/workspaces/{workspace}/members",
        json={"email": "outside@example.com", "role": "viewer"},
    )
    assert member.status_code == 200
    assert outsider.get(f"/api/v1/workflows/{wid}/plan").status_code == 200
    assert (
        outsider.post(
            "/api/v1/workflows",
            json={
                "workspace_id": workspace,
                "prompt": "Analyze",
                "csv_text": "a\n1",
                "planner_mode": "llm",
            },
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("planning_max_tokens", 0),
        ("planning_max_tokens", True),
        ("planning_max_cost_usd", -1),
        ("planning_max_cost_usd", True),
        ("planning_deadline_seconds", 181),
    ],
)
def test_invalid_budgets_are_rejected_before_workflow_creation(runtime, field, value):
    client, workspace, sessions, install, _ = runtime
    provider = install([proposal()])
    response = client.post(
        "/api/v1/workflows",
        json={
            "workspace_id": workspace,
            "prompt": "Analyze",
            "csv_text": "a\n1",
            "planner_mode": "llm",
            field: value,
        },
    )
    assert response.status_code == 422 and provider.calls == []
    with sessions() as db:
        assert db.scalars(select(Workflow)).all() == []


def test_nonfinite_budget_and_password_errors_do_not_echo_raw_inputs(runtime):
    client, workspace, _, _, _ = runtime
    body = json.dumps(
        {
            "workspace_id": workspace,
            "prompt": "Analyze",
            "csv_text": "a\n1",
            "planner_mode": "llm",
            "planning_max_cost_usd": float("nan"),
        }
    )
    response = client.post(
        "/api/v1/workflows", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422 and '"input"' not in response.text
    response = client.post(
        "/api/v1/auth/login", json={"email": "planner@example.com", "password": "shh!"}
    )
    assert response.status_code == 422 and "shh!" not in response.text


def test_disabled_required_agent_prevents_paid_request(runtime, monkeypatch):
    from app import planner
    from app.registry import build_registry

    _, _, sessions, install, create = runtime
    provider = install([proposal()])
    wid = create()
    monkeypatch.setattr(planner, "REGISTRY", build_registry(("csv_inspector",)))
    worker.tick()
    assert provider.calls == []
    assert read(sessions, wid).planning["error_code"] == "planning_agents_unavailable"


def test_invalid_csv_profile_prevents_paid_request(runtime):
    _, _, sessions, install, create = runtime
    provider = install([proposal()])
    wid = create(csv_text="a,b\n")
    worker.tick()
    assert provider.calls == []
    assert read(sessions, wid).planning["error_code"] == "planning_input_invalid"
