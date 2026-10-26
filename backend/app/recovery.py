"""Durable checkpoint recovery, pause/resume state machine, and retry handling."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Event, Task, Workflow, now


class RecoveryError(Exception):
    """Raised when a workflow recovery, pause, or resume transition is invalid."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def pause_workflow(db: Session, workflow_id: str, user_id: str | None = None) -> Workflow:
    """Pause an active or queued workflow, persisting its checkpoint boundary."""
    workflow = db.get(Workflow, workflow_id)
    if workflow is None:
        raise RecoveryError("Workflow not found", status_code=404)

    if workflow.status in ("succeeded", "failed", "cancelled"):
        raise RecoveryError(f"Cannot pause a terminated workflow in status '{workflow.status}'")

    if workflow.status == "paused":
        return workflow

    tasks = list(
        db.scalars(
            select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal.asc())
        ).all()
    )

    succeeded_tasks = [t.name for t in tasks if t.status == "succeeded"]
    pending_tasks = [t.name for t in tasks if t.status in ("pending", "ready")]
    running_tasks = [t.name for t in tasks if t.status == "running"]

    recovery_meta = dict(workflow.recovery_metadata or {})
    pause_history = list(recovery_meta.get("pause_history", []))
    pause_history.append(
        {
            "paused_at": now().isoformat(),
            "paused_by": user_id,
            "checkpoint_task_count": len(succeeded_tasks),
        }
    )
    recovery_meta["pause_history"] = pause_history
    recovery_meta["current_checkpoint"] = {
        "status": "paused",
        "succeeded_tasks": succeeded_tasks,
        "pending_tasks": pending_tasks,
        "running_tasks": running_tasks,
        "timestamp": now().isoformat(),
    }
    workflow.recovery_metadata = recovery_meta
    workflow.status = "paused"

    db.add(
        Event(
            workflow_id=workflow.id,
            kind="workflow_paused",
            details={
                "requested_by": user_id,
                "succeeded_tasks_checkpointed": len(succeeded_tasks),
                "timestamp": now().isoformat(),
            },
        )
    )
    db.commit()
    db.refresh(workflow)
    return workflow


def resume_workflow(db: Session, workflow_id: str, user_id: str | None = None) -> Workflow:
    """Resume an interrupted or paused workflow from its last checkpointed task boundary."""
    workflow = db.get(Workflow, workflow_id)
    if workflow is None:
        raise RecoveryError("Workflow not found", status_code=404)

    if workflow.status != "paused":
        if workflow.status in ("queued", "running"):
            return workflow
        raise RecoveryError(
            f"Cannot resume workflow in status '{workflow.status}'. Only paused workflows can be resumed."
        )

    # Keep active leases intact. An in-flight worker may still commit safely; if it
    # died while paused, the normal lease-expiry recovery path will requeue its task.

    recovery_meta = dict(workflow.recovery_metadata or {})
    resume_history = list(recovery_meta.get("resume_history", []))
    resume_history.append(
        {
            "resumed_at": now().isoformat(),
            "resumed_by": user_id,
        }
    )
    recovery_meta["resume_history"] = resume_history
    recovery_meta["resumed_from_checkpoint"] = True
    workflow.recovery_metadata = recovery_meta
    workflow.status = "queued"

    db.add(
        Event(
            workflow_id=workflow.id,
            kind="workflow_resumed",
            details={
                "requested_by": user_id,
                "resumed_from_checkpoint": True,
                "timestamp": now().isoformat(),
            },
        )
    )
    db.commit()
    db.refresh(workflow)
    return workflow


def retry_workflow(
    db: Session,
    workflow_id: str,
    user_id: str | None = None,
    preserve_checkpoint: bool = True,
) -> Workflow:
    """Retry a failed or cancelled workflow, optionally preserving succeeded checkpoints."""
    workflow = db.get(Workflow, workflow_id)
    if workflow is None:
        raise RecoveryError("Workflow not found", status_code=404)

    if workflow.status in ("queued", "running"):
        raise RecoveryError("Cannot retry an in-flight workflow. Cancel or wait for completion.")
    if workflow.status not in ("failed", "cancelled"):
        raise RecoveryError(
            f"Cannot retry workflow in status '{workflow.status}'. Only failed or cancelled workflows can be retried."
        )

    tasks = list(
        db.scalars(
            select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal.asc())
        ).all()
    )

    if preserve_checkpoint:
        # Preserve succeeded tasks, reset failed, skipped, cancelled, or waiting_approval tasks
        for task in tasks:
            if task.status != "succeeded":
                task.status = "pending"
                task.result = None
                task.result_envelope = None
                task.error = None
                task.error_code = None
                task.lease_owner = None
                task.lease_expires_at = None
    else:
        # Full replay: reset all tasks
        for task in tasks:
            task.status = "pending"
            task.result = None
            task.result_envelope = None
            task.error = None
            task.error_code = None
            task.lease_owner = None
            task.lease_expires_at = None

    recovery_meta = dict(workflow.recovery_metadata or {})
    retry_history = list(recovery_meta.get("retry_history", []))
    retry_history.append(
        {
            "retried_at": now().isoformat(),
            "retried_by": user_id,
            "preserve_checkpoint": preserve_checkpoint,
        }
    )
    recovery_meta["retry_history"] = retry_history
    workflow.recovery_metadata = recovery_meta
    workflow.status = "queued"
    workflow.error = None
    workflow.output = None

    db.add(
        Event(
            workflow_id=workflow.id,
            kind="workflow_retried",
            details={
                "requested_by": user_id,
                "preserve_checkpoint": preserve_checkpoint,
                "timestamp": now().isoformat(),
            },
        )
    )
    db.commit()
    db.refresh(workflow)
    return workflow


def get_workflow_checkpoint(db: Session, workflow_id: str) -> dict[str, Any]:
    """Retrieve detailed checkpoint status and recovery capabilities for a workflow."""
    workflow = db.get(Workflow, workflow_id)
    if workflow is None:
        raise RecoveryError("Workflow not found", status_code=404)

    tasks = list(
        db.scalars(
            select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal.asc())
        ).all()
    )

    succeeded = [t.name for t in tasks if t.status == "succeeded"]
    pending = [t.name for t in tasks if t.status in ("pending", "ready")]
    running = [t.name for t in tasks if t.status == "running"]
    failed = [t.name for t in tasks if t.status == "failed"]
    cancelled = [t.name for t in tasks if t.status == "cancelled"]

    return {
        "workflow_id": workflow.id,
        "status": workflow.status,
        "total_tasks": len(tasks),
        "succeeded_tasks": succeeded,
        "pending_tasks": pending,
        "running_tasks": running,
        "failed_tasks": failed,
        "cancelled_tasks": cancelled,
        "checkpoint_progress": (len(succeeded) / len(tasks)) if tasks else 0.0,
        "recovery_metadata": workflow.recovery_metadata or {},
        "can_pause": workflow.status in ("queued", "running"),
        "can_resume": workflow.status == "paused",
        "can_retry": workflow.status in ("failed", "cancelled"),
    }
