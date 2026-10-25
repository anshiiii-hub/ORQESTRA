import logging
import time
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import AgentFailure, AgentRequest
from .db import SessionLocal
from .dispatch import dispatch
from .heartbeats import HEARTBEAT_INTERVAL, record_heartbeat
from .migrate import require_current_schema
from .models import Event, Task, Workflow
from .registry import REGISTRY, RegistryError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("orqestra.worker")


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


def tick() -> bool:
    # One worker only; heartbeat records do not replace task leases.
    request = None
    failure = None
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
        tasks = list(
            db.scalars(
                select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal)
            ).all()
        )
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
            workflow.status, workflow.output = "succeeded", tasks[-1].result
            db.add(Event(workflow_id=workflow.id, kind="workflow_succeeded"))
            return True
        ready = next(
            (
                task
                for task in tasks
                if task.status == "pending"
                and all(
                    parent in by_name and by_name[parent].status == "succeeded"
                    for parent in task.dependencies
                )
            ),
            None,
        )
        if ready is None:
            if not any(task.status == "running" for task in tasks):
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
        workflow, task = db.get(Workflow, wid), db.get(Task, tid)
        if workflow is None or task is None:
            return False
        if workflow.status not in ("queued", "running"):
            task.status = "cancelled"
            db.add(Event(workflow_id=wid, kind="task_cancelled", details={"task": task.name}))
        elif failure is not None:
            task.status, task.error_code = "failed", failure.code
            task.error = f"{failure.code}: {failure.message}"
            task.result_envelope = outcome.model_dump(mode="json") if outcome else None
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
                task.status = "pending"
                kind = "task_requeued_after_restart"
            else:
                task.status = "cancelled"
                kind = "task_cancelled"
            db.add(
                Event(
                    workflow_id=task.workflow_id,
                    kind=kind,
                    details={"task": task.name, "reason": "worker_restart"},
                )
            )


def main():
    require_current_schema()
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
