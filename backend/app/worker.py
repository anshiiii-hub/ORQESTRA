import logging
import time
from sqlalchemy import select
from .db import Base, engine, SessionLocal
from .models import Workflow, Task, Event
from .agents import AGENTS, AgentError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("orchestra.worker")


def tick() -> bool:
    # One worker is supported in Checkpoint 1. Multiple workers require per-task leases.
    with SessionLocal.begin() as db:
        w = db.scalars(select(Workflow).where(Workflow.status.in_(["queued", "running"]))
                       .order_by(Workflow.created_at).with_for_update(skip_locked=True).limit(1)).first()
        if w is None: return False
        tasks = db.scalars(select(Task).where(Task.workflow_id == w.id).order_by(Task.ordinal)).all()
        if w.status == "queued":
            w.status = "running"
            db.add(Event(workflow_id=w.id, kind="workflow_started"))
        by_name = {t.name: t for t in tasks}
        ready = next((t for t in tasks if t.status == "pending" and all(by_name[d].status == "succeeded" for d in t.dependencies)), None)
        if ready is None:
            if all(t.status == "succeeded" for t in tasks):
                w.status = "succeeded"
                w.output = by_name["verify"].result
                db.add(Event(workflow_id=w.id, kind="workflow_succeeded"))
            elif any(t.status == "failed" for t in tasks):
                w.status = "failed"
                w.error = "A task failed; see task errors"
                db.add(Event(workflow_id=w.id, kind="workflow_failed"))
            return True
        ready.status = "running"
        ready.attempts += 1
        db.add(Event(workflow_id=w.id, kind="task_started", details={"task": ready.name, "agent": ready.agent}))
        wid, tid = w.id, ready.id
    # Agent executes without DB locks. Cancellation is checked before persisting results.
    try:
        with SessionLocal() as db:
            wf = db.get(Workflow, wid)
            dep_tasks = db.scalars(select(Task).where(Task.workflow_id == wid)).all()
            inputs = {t.name: t.result for t in dep_tasks if t.result is not None}
            csv_text = wf.input_csv
        result = AGENTS[ready.agent](csv_text, inputs)
        error = None
    except (AgentError, Exception) as exc:
        result, error = None, f"{type(exc).__name__}: {str(exc)[:300]}"
        log.warning("Task %s failed: %s", tid, error)
    with SessionLocal.begin() as db:
        wf = db.get(Workflow, wid)
        task = db.get(Task, tid)
        if wf.status == "cancelled":
            task.status = "cancelled"
            db.add(Event(workflow_id=wid, kind="task_cancelled", details={"task": task.name}))
        elif error is None:
            task.status, task.result = "succeeded", result
            db.add(Event(workflow_id=wid, kind="task_succeeded", details={"task": task.name}))
        else:
            task.status, task.error = "failed", error
            db.add(Event(workflow_id=wid, kind="task_failed", details={"task": task.name, "error": error}))
    return True


def recover_running_tasks():
    # Only active workflows may resume pure CSV tasks after the sole worker restarts.
    with SessionLocal.begin() as db:
        stuck = db.execute(select(Task, Workflow).join(Workflow, Task.workflow_id == Workflow.id)
                           .where(Task.status == "running").with_for_update()).all()
        for task, workflow in stuck:
            if workflow.status in ("queued", "running"):
                task.status = "pending"
                kind = "task_requeued_after_restart"
            else:
                task.status = "cancelled"
                kind = "task_cancelled"
            db.add(Event(workflow_id=task.workflow_id, kind=kind,
                         details={"task": task.name, "reason": "worker_restart"}))


def main():
    Base.metadata.create_all(engine)
    recover_running_tasks()
    while True:
        try:
            if not tick(): time.sleep(1)
        except Exception:
            log.exception("Worker loop error")
            time.sleep(2)

if __name__ == "__main__": main()
