"""Persist reservations before paid planning; never replay an uncertain provider call."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .llm import (
    Completion,
    OpenAIPlannerProvider,
    PlannerSettings,
    PlanningError,
    money,
    planner_settings,
)
from .models import Event, Task, Workflow, now
from .planner import demo_plan
from .planning import PlannerProposal, input_token_bound, planning_messages

MAX_PLANNING_ATTEMPTS = 2


def make_provider(settings: PlannerSettings):
    return OpenAIPlannerProvider(settings)


def initial_state(
    settings: PlannerSettings, max_tokens: int, max_cost_usd: float, deadline_seconds: int
) -> dict:
    return {
        "schema_version": "1.0",
        **settings.snapshot(),
        "status": "pending",
        "attempts": [],
        "deadline_at": (now() + timedelta(seconds=deadline_seconds)).isoformat(),
        "max_tokens": max_tokens,
        "max_cost_usd": str(max_cost_usd),
        "accounted_tokens": 0,
        "accounted_cost_usd": money(Decimal(0)),
        "error_code": None,
    }


def add_tasks(db: Session, workflow: Workflow, proposal: PlannerProposal) -> None:
    for ordinal, task in enumerate(proposal.ordered_tasks()):
        db.add(
            Task(
                workflow_id=workflow.id,
                name=task.id,
                agent=task.agent,
                capability=task.capability,
                agent_version=task.agent_version,
                schema_version=task.schema_version,
                dependencies=task.dependencies,
                input_bindings=task.input_bindings,
                ordinal=ordinal,
            )
        )
    workflow.execution_plan = proposal.model_dump(mode="json")


def copy_state(workflow: Workflow) -> dict:
    if workflow.planning is None:
        raise PlanningError("planning_state_invalid", "Planning state is unavailable")
    return deepcopy(workflow.planning)


def totals(state: dict) -> None:
    state["accounted_tokens"] = sum(attempt["accounted_tokens"] for attempt in state["attempts"])
    state["accounted_cost_usd"] = money(
        sum((Decimal(attempt["accounted_cost_usd"]) for attempt in state["attempts"]), Decimal(0))
    )


def fail_planning(db: Session, workflow: Workflow, error: PlanningError) -> None:
    state = deepcopy(workflow.planning) or {"schema_version": "1.0", "attempts": []}
    for attempt in state["attempts"]:
        if attempt["status"] == "reserved":
            attempt["status"] = "indeterminate"
            attempt["error_code"] = error.code
    state["error_code"] = error.code
    state["status"] = "cancelled" if workflow.status == "cancelled" else "failed"
    totals(state)
    workflow.planning = state
    db.add(
        Event(workflow_id=workflow.id, kind="planning_failed", details={"error_code": error.code})
    )
    if workflow.status != "cancelled":
        workflow.status, workflow.error = "failed", f"{error.code}: {str(error)}"
        db.add(
            Event(
                workflow_id=workflow.id, kind="workflow_failed", details={"error_code": error.code}
            )
        )


def claim_planning(sessions: sessionmaker[Session]) -> str | None:
    with sessions.begin() as db:
        workflow = db.scalars(
            select(Workflow)
            .where(
                Workflow.status == "queued", Workflow.planning["status"].as_string() == "pending"
            )
            .order_by(Workflow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if workflow is None:
            return None
        state = copy_state(workflow)
        state["status"] = "running"
        workflow.planning, workflow.status = state, "planning"
        db.add(
            Event(
                workflow_id=workflow.id,
                kind="planning_started",
                details={"provider": "openai", "model": state["model"]},
            )
        )
        return workflow.id


def reserve_attempt(
    db: Session, workflow: Workflow, settings: PlannerSettings, messages: list[dict[str, str]]
) -> tuple[int, int, float]:
    state = copy_state(workflow)
    if state is None or state["status"] != "running" or workflow.status != "planning":
        raise PlanningError("planning_cancelled", "Planning is no longer active")
    remaining_seconds = (datetime.fromisoformat(state["deadline_at"]) - now()).total_seconds()
    if remaining_seconds <= 0:
        raise PlanningError("planning_deadline", "Planning deadline expired")
    if len(state["attempts"]) >= MAX_PLANNING_ATTEMPTS:
        raise PlanningError(
            "planning_invalid_plan", "No valid plan was produced within the repair limit"
        )
    bound = input_token_bound(messages)
    output_limit = min(
        settings.max_output_tokens, state["max_tokens"] - state["accounted_tokens"] - bound
    )
    if output_limit < 256:
        raise PlanningError(
            "planning_token_budget", "Planning token budget cannot cover another request"
        )
    cost = settings.cost(bound, output_limit)
    if Decimal(state["accounted_cost_usd"]) + cost > Decimal(state["max_cost_usd"]):
        raise PlanningError(
            "planning_cost_budget", "Planning cost budget cannot cover another request"
        )
    attempt = {
        "number": len(state["attempts"]) + 1,
        "status": "reserved",
        "input_token_bound": bound,
        "output_token_limit": output_limit,
        "reserved_cost_usd": money(cost),
        "accounted_tokens": bound + output_limit,
        "accounted_cost_usd": money(cost),
        "input_tokens": None,
        "output_tokens": None,
        "error_code": None,
    }
    state["attempts"].append(attempt)
    totals(state)
    workflow.planning = state
    db.add(
        Event(
            workflow_id=workflow.id,
            kind="planning_attempt_started",
            details={
                "attempt": attempt["number"],
                "reserved_tokens": attempt["accounted_tokens"],
                "reserved_cost_usd": money(cost),
            },
        )
    )
    return bound, output_limit, min(remaining_seconds, 30.0)


def record_completion(
    db: Session, workflow: Workflow, completion: Completion, settings: PlannerSettings
) -> bool:
    state = copy_state(workflow)
    attempt = state["attempts"][-1]
    if (
        type(completion.input_tokens) is not int
        or type(completion.output_tokens) is not int
        or not 0 <= completion.input_tokens <= attempt["input_token_bound"]
        or not 0 <= completion.output_tokens <= attempt["output_token_limit"]
    ):
        raise PlanningError(
            "provider_usage_invalid", "Planning provider returned unverifiable token usage"
        )
    attempt.update(
        status="completed",
        input_tokens=completion.input_tokens,
        output_tokens=completion.output_tokens,
        accounted_tokens=completion.input_tokens + completion.output_tokens,
        accounted_cost_usd=money(settings.cost(completion.input_tokens, completion.output_tokens)),
    )
    totals(state)
    workflow.planning = state
    db.add(
        Event(
            workflow_id=workflow.id,
            kind="planning_attempt_completed",
            details={
                "attempt": attempt["number"],
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "cost_usd": attempt["accounted_cost_usd"],
            },
        )
    )
    return workflow.status == "planning"


def run_planning(workflow_id: str, sessions: sessionmaker[Session]) -> None:
    try:
        settings = planner_settings()
        if not settings.enabled:
            raise PlanningError("provider_not_configured", "LLM planning is disabled")
        try:
            demo_plan()
        except ValidationError:
            raise PlanningError(
                "planning_agents_unavailable", "Required CSV agents are unavailable"
            ) from None
        provider = make_provider(settings)
        for attempt_number in range(MAX_PLANNING_ATTEMPTS):
            with sessions.begin() as db:
                workflow = db.get(Workflow, workflow_id, with_for_update=True)
                if workflow is None or workflow.status != "planning":
                    return
                state = workflow.planning
                if state is None or any(
                    state[key] != value for key, value in settings.snapshot().items()
                ):
                    raise PlanningError(
                        "planning_configuration_changed",
                        "Provider configuration changed after workflow submission",
                    )
                messages = planning_messages(
                    workflow.prompt, workflow.input_csv, repair=attempt_number > 0
                )
                bound, output_limit, timeout = reserve_attempt(db, workflow, settings, messages)
            # The reservation is committed before any network request.
            with sessions.begin() as db:
                workflow = db.get(Workflow, workflow_id)
                if workflow is None:
                    return
                if workflow.status != "planning":
                    state = copy_state(workflow)
                    state["attempts"][-1].update(
                        status="cancelled_before_send",
                        accounted_tokens=0,
                        accounted_cost_usd=money(Decimal(0)),
                    )
                    totals(state)
                    workflow.planning = state
                    return
                state = copy_state(workflow)
                remaining = (datetime.fromisoformat(state["deadline_at"]) - now()).total_seconds()
                if remaining <= 0:
                    state["attempts"][-1].update(
                        status="not_sent", accounted_tokens=0, accounted_cost_usd=money(Decimal(0))
                    )
                    totals(state)
                    workflow.planning = state
                    fail_planning(
                        db,
                        workflow,
                        PlanningError(
                            "planning_deadline", "Planning deadline expired before dispatch"
                        ),
                    )
                    return
                timeout = min(timeout, remaining)
            completion = provider.complete(messages, output_limit, timeout, bound)
            with sessions.begin() as db:
                workflow = db.get(Workflow, workflow_id, with_for_update=True)
                if workflow is None:
                    return
                if not record_completion(db, workflow, completion, settings):
                    return
            try:
                if completion.truncated:
                    raise ValueError("Truncated proposal")
                proposal = PlannerProposal.model_validate_json(completion.content)
            except (ValidationError, ValueError):
                with sessions.begin() as db:
                    workflow = db.get(Workflow, workflow_id)
                    if workflow is None or workflow.status != "planning":
                        return
                    state = copy_state(workflow)
                    state["attempts"][-1]["error_code"] = "planning_invalid_plan"
                    workflow.planning = state
                    if attempt_number == 0:
                        db.add(Event(workflow_id=workflow_id, kind="planning_repair_requested"))
                continue
            with sessions.begin() as db:
                workflow = db.get(Workflow, workflow_id, with_for_update=True)
                if workflow is None or workflow.status != "planning":
                    return
                state = copy_state(workflow)
                if datetime.fromisoformat(state["deadline_at"]) <= now():
                    raise PlanningError("planning_deadline", "Planning deadline expired")
                add_tasks(db, workflow, proposal)
                state["status"] = "succeeded"
                workflow.planning, workflow.status = state, "queued"
                db.add(
                    Event(
                        workflow_id=workflow.id,
                        kind="plan_validated",
                        details={
                            "task_count": len(proposal.tasks),
                            "final_task_id": proposal.final_task_id,
                            "provider": "openai",
                        },
                    )
                )
            return
        raise PlanningError(
            "planning_invalid_plan", "No valid plan was produced within the repair limit"
        )
    except Exception as error:
        failure = (
            error
            if isinstance(error, PlanningError)
            else PlanningError("planning_failed", "Planning could not be completed safely")
        )
        with sessions.begin() as db:
            workflow = db.get(Workflow, workflow_id, with_for_update=True)
            if workflow is not None:
                fail_planning(db, workflow, failure)


def recover_planning(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as db:
        workflows = db.scalars(
            select(Workflow).where(Workflow.status.in_(["planning", "cancelled"])).with_for_update()
        ).all()
        for workflow in workflows:
            state = deepcopy(workflow.planning)
            if workflow.status == "cancelled":
                if state and any(attempt["status"] == "reserved" for attempt in state["attempts"]):
                    fail_planning(
                        db,
                        workflow,
                        PlanningError(
                            "planning_interrupted",
                            "Cancelled planning has an uncertain provider charge",
                        ),
                    )
                continue
            if state and not state["attempts"]:
                state["status"] = "pending"
                workflow.planning, workflow.status = state, "queued"
                db.add(Event(workflow_id=workflow.id, kind="planning_requeued_after_restart"))
            else:
                fail_planning(
                    db,
                    workflow,
                    PlanningError(
                        "planning_interrupted",
                        "Planning was interrupted; an uncertain provider call will not be replayed",
                    ),
                )
