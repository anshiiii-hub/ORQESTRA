import logging
import time
from datetime import timedelta
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import AgentFailure, AgentRequest
from .db import SessionLocal
from .dispatch import dispatch
from .heartbeats import HEARTBEAT_INTERVAL, WORKER_ID, record_heartbeat
from .migrate import require_current_schema
from .models import Event, Task, TaskAttempt, TaskDependency, Workflow, now
from .planning_runtime import claim_planning, recover_planning, run_planning
from .registry import REGISTRY, RegistryError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("orqestra.worker")
TASK_LEASE_SECONDS = 360


def prepare_request(
    workflow: Workflow, task: Task, tasks: list[Task], request_id: str
) -> AgentRequest:
    definition = REGISTRY.select(
        task.capability,
        agent_id=task.agent,
        schema_version=task.schema_version,
        agent_version=task.agent_version,
    )
    required = dict(definition.required_context)
    if set(task.input_bindings) != set(required):
        raise ValueError("Invalid input bindings")
    by_name = {parent.name: parent for parent in tasks}
    context, references = {}, {}
    for alias, parent_name in task.input_bindings.items():
        parent = by_name.get(parent_name)
        if (
            parent is None
            or parent.name not in task.dependencies
            or parent.status != "succeeded"
            or parent.capability != required[alias]
        ):
            raise ValueError("Invalid input dependency")
        context[alias] = parent.result
        references[alias] = parent.id
    return AgentRequest.model_validate(
        {
            "schema_version": task.schema_version,
            "request_id": request_id,
            "workflow_id": workflow.id,
            "task_id": task.id,
            "agent_type": task.agent,
            "agent_version": task.agent_version,
            "capability": task.capability,
            "objective": workflow.prompt,
            "context_refs": references,
            "payload": {"csv_text": workflow.input_csv, "context": context},
            "tool_permissions": [],
        }
    )


def fail_workflow(db: Session, workflow: Workflow, tasks: list[Task], error: AgentFailure) -> None:
    workflow.status = "failed"
    workflow.error = f"{error.code}: {error.message}"
    for task in tasks:
        if task.status == "pending":
            task.status = "skipped"
    db.add(
        Event(workflow_id=workflow.id, kind="workflow_failed", details={"error_code": error.code})
    )


def tick(worker_id: str = WORKER_ID) -> bool:
    """Claim one ready node durably, execute without a DB lock, then fence its result."""
    planning_id = claim_planning(SessionLocal)
    if planning_id is not None:
        run_planning(planning_id, SessionLocal)
        return True
    request = None
    failure = None
    attempt_id = None
    with SessionLocal.begin() as db:
        workflow = db.scalars(
            select(Workflow)
            .where(Workflow.status.in_(["queued", "running"]))
            .order_by(Workflow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if workflow is None:
            return False
        if workflow.planning and workflow.planning.get("status") in ("pending", "running"):
            # Another worker may currently own planning, or the planner is queued.
            return False
        tasks = list(
            db.scalars(
                select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal)
            ).all()
        )
        task_names = {task.id: task.name for task in tasks}
        dependencies_by_id: dict[str, set[str]] = {task.id: set() for task in tasks}
        dependency_edges_valid = True
        for edge in db.scalars(
            select(TaskDependency).where(TaskDependency.workflow_id == workflow.id)
        ):
            if edge.task_id in dependencies_by_id and edge.dependency_task_id in task_names:
                dependencies_by_id[edge.task_id].add(task_names[edge.dependency_task_id])
            else:
                dependency_edges_valid = False
        if not dependency_edges_valid or any(
            len(task.dependencies) != len(set(task.dependencies))
            or set(task.dependencies) != dependencies_by_id[task.id]
            for task in tasks
        ):
            fail_workflow(
                db,
                workflow,
                tasks,
                AgentFailure(
                    code="invalid_input",
                    stage="input",
                    message="Task dependency records do not match the execution plan",
                ),
            )
            return True
        if workflow.status == "queued":
            workflow.status = "running"
            db.add(Event(workflow_id=workflow.id, kind="workflow_started"))
        by_name = {task.name: task for task in tasks}
        if any(task.status == "failed" for task in tasks):
            fail_workflow(
                db,
                workflow,
                tasks,
                AgentFailure(
                    code="execution_failed", stage="execution", message="A workflow task failed"
                ),
            )
            return True
        if tasks and all(task.status == "succeeded" for task in tasks):
            final_name = (
                workflow.execution_plan.get("final_task_id")
                if workflow.execution_plan
                else tasks[-1].name
            )
            if final_name not in by_name:
                fail_workflow(
                    db,
                    workflow,
                    tasks,
                    AgentFailure(
                        code="invalid_input",
                        stage="input",
                        message="Workflow final task is unavailable",
                    ),
                )
                return True
            workflow.status, workflow.output = "succeeded", by_name[final_name].result
            db.add(Event(workflow_id=workflow.id, kind="workflow_succeeded"))
            return True
        ready = next(
            (
                task
                for task in tasks
                if task.status in ("pending", "ready")
                and all(
                    parent in by_name and by_name[parent].status == "succeeded"
                    for parent in dependencies_by_id[task.id]
                )
            ),
            None,
        )
        if ready is None:
            current = now()
            expired = next(
                (
                    task
                    for task in tasks
                    if task.status == "running"
                    and (
                        task.lease_expires_at is None
                        or task.lease_expires_at.replace(tzinfo=current.tzinfo) <= current
                    )
                ),
                None,
            )
            if expired is not None:
                previous = (
                    db.get(TaskAttempt, expired.current_attempt_id)
                    if expired.current_attempt_id
                    else None
                )
                if previous is not None and previous.status == "running":
                    previous.status, previous.finished_at = "lease_expired", current
                    previous.error_code = "worker_lease_expired"
                expired.status = "pending"
                expired.current_attempt_id = expired.lease_owner = None
                expired.lease_expires_at = None
                db.add(
                    Event(
                        workflow_id=workflow.id,
                        kind="task_lease_expired",
                        details={"task": expired.name},
                    )
                )
                return True
        if ready is None:
            if not any(task.status in ("running", "waiting_approval") for task in tasks):
                fail_workflow(
                    db,
                    workflow,
                    tasks,
                    AgentFailure(
                        code="invalid_input",
                        stage="input",
                        message="Workflow dependencies cannot be scheduled",
                    ),
                )
                return True
            return False
        ready.status = "running"
        ready.attempts += 1
        ready.result, ready.result_envelope, ready.error, ready.error_code = None, None, None, None
        request_id = str(uuid4())
        try:
            request = prepare_request(workflow, ready, tasks, request_id)
            ready.request_envelope = request.model_dump(mode="json")
        except RegistryError as error:
            failure = AgentFailure.model_validate(
                {"code": error.code, "stage": "dispatch", "message": str(error)}
            )
        except (ValidationError, ValueError, TypeError, KeyError):
            failure = AgentFailure(
                code="invalid_input",
                stage="input",
                message="Task input did not satisfy its contract",
            )
        current = now()
        attempt_id = str(uuid4())
        expires_at = current + timedelta(seconds=TASK_LEASE_SECONDS)
        attempt = TaskAttempt(
            id=attempt_id,
            task_id=ready.id,
            workflow_id=workflow.id,
            attempt_number=ready.attempts,
            idempotency_key=f"{ready.id}:{ready.attempts}",
            worker_id=worker_id,
            status="running" if failure is None else "failed",
            lease_expires_at=expires_at,
            request_id=request_id,
            request_envelope=request.model_dump(mode="json") if request is not None else None,
            error_code=failure.code if failure else None,
            started_at=current,
            finished_at=current if failure else None,
        )
        db.add(attempt)
        ready.current_attempt_id = attempt_id
        ready.lease_owner = worker_id if failure is None else None
        ready.lease_expires_at = expires_at if failure is None else None
        db.add(
            Event(
                workflow_id=workflow.id,
                kind="task_started",
                details={
                    "task": ready.name,
                    "agent": ready.agent,
                    "agent_version": ready.agent_version,
                    "capability": ready.capability,
                    "schema_version": ready.schema_version,
                    "request_id": request_id,
                },
            )
        )
        wid, tid = workflow.id, ready.id
    outcome = dispatch(request, registry=REGISTRY) if request is not None else None
    if outcome is not None:
        failure = outcome.error
    with SessionLocal.begin() as db:
        workflow = db.get(Workflow, wid)
        task = db.get(Task, tid)
        completed_attempt = db.get(TaskAttempt, attempt_id)
        if workflow is None or task is None or completed_attempt is None:
            return False
        owns_attempt = (
            completed_attempt.status == "running"
            and task.status == "running"
            and task.current_attempt_id == completed_attempt.id
            and task.lease_owner == worker_id
        ) or (
            completed_attempt.status == "failed"
            and failure is not None
            and task.status == "running"
            and task.current_attempt_id == completed_attempt.id
        )
        active = workflow.status in ("queued", "running")
        if workflow.status == "paused" and task.current_attempt_id == completed_attempt.id:
            completed_attempt.status = "superseded"
            completed_attempt.finished_at = now()
            completed_attempt.error_code = "workflow_paused"
            task.status = "pending"
            task.current_attempt_id = task.lease_owner = None
            task.lease_expires_at = None
            db.add(
                Event(
                    workflow_id=wid,
                    kind="task_requeued_after_pause",
                    details={"task": task.name},
                )
            )
        elif not owns_attempt or not active:
            if completed_attempt.status == "running":
                completed_attempt.status = "cancelled" if not active else "superseded"
                completed_attempt.finished_at = now()
                completed_attempt.error_code = (
                    "workflow_cancelled" if not active else "lease_superseded"
                )
            if task.current_attempt_id == completed_attempt.id:
                task.status = "cancelled"
                task.current_attempt_id = task.lease_owner = None
                task.lease_expires_at = None
                db.add(Event(workflow_id=wid, kind="task_cancelled", details={"task": task.name}))
        elif failure is not None:
            task.status, task.error_code = "failed", failure.code
            task.error = f"{failure.code}: {failure.message}"
            task.result_envelope = outcome.model_dump(mode="json") if outcome else None
            completed_attempt.status = "failed"
            completed_attempt.finished_at = now()
            completed_attempt.error_code = failure.code
            completed_attempt.result_envelope = outcome.model_dump(mode="json") if outcome else None
            task.current_attempt_id = task.lease_owner = None
            task.lease_expires_at = None
            db.add(
                Event(
                    workflow_id=wid,
                    kind="task_failed",
                    details={
                        "task": task.name,
                        "error_code": failure.code,
                        "stage": failure.stage,
                        "error": failure.message,
                    },
                )
            )
            tasks = list(db.scalars(select(Task).where(Task.workflow_id == wid)).all())
            fail_workflow(db, workflow, tasks, failure)
            log.warning("Task %s failed with code %s", tid, failure.code)
        elif outcome is not None:
            task.status, task.result = "succeeded", outcome.structured_output
            task.result_envelope = outcome.model_dump(mode="json")
            completed_attempt.status, completed_attempt.finished_at = "succeeded", now()
            completed_attempt.result_envelope = outcome.model_dump(mode="json")
            task.current_attempt_id = task.lease_owner = None
            task.lease_expires_at = None
            db.add(
                Event(
                    workflow_id=wid,
                    kind="task_succeeded",
                    details={
                        "task": task.name,
                        "request_id": outcome.request_id,
                        "schema_version": outcome.schema_version,
                    },
                )
            )
    return True


def recover_running_tasks():
    with SessionLocal.begin() as db:
        stuck = db.execute(
            select(Task, Workflow)
            .join(Workflow, Task.workflow_id == Workflow.id)
            .where(Task.status == "running")
            .with_for_update()
        ).all()
        for task, workflow in stuck:
            if workflow.status in ("queued", "running"):
                current = now()
                if (
                    task.lease_expires_at is not None
                    and task.lease_expires_at.replace(tzinfo=current.tzinfo) > current
                ):
                    continue
                attempt = (
                    db.get(TaskAttempt, task.current_attempt_id)
                    if task.current_attempt_id
                    else None
                )
                if attempt is not None and attempt.status == "running":
                    attempt.status, attempt.finished_at = "lease_expired", current
                    attempt.error_code = "worker_lease_expired"
                task.status = "pending"
                task.current_attempt_id = task.lease_owner = None
                task.lease_expires_at = None
                kind = "task_requeued_after_restart"
            else:
                task.status = "cancelled"
                attempt = (
                    db.get(TaskAttempt, task.current_attempt_id)
                    if task.current_attempt_id
                    else None
                )
                if attempt is not None and attempt.status == "running":
                    attempt.status, attempt.finished_at = "cancelled", now()
                    attempt.error_code = "workflow_cancelled"
                task.current_attempt_id = task.lease_owner = None
                task.lease_expires_at = None
                kind = "task_cancelled"
            db.add(
                Event(
                    workflow_id=task.workflow_id,
                    kind=kind,
                    details={"task": task.name, "reason": "lease_expired"},
                )
            )


def main():
    require_current_schema()
    recover_planning(SessionLocal)
    recover_running_tasks()
    next_heartbeat = 0.0
    while True:
        try:
            if time.monotonic() >= next_heartbeat:
                with SessionLocal.begin() as db:
                    record_heartbeat(db)
                next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
            if not tick():
                time.sleep(1)
        except Exception:
            log.exception("Worker loop error")
            time.sleep(2)


if __name__ == "__main__":
    main()
