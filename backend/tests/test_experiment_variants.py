"""Ablation switches must remove only their named protection."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import worker
from app.models import Task, TaskAttempt, Workflow, now


def seed(sessions):
    with sessions.begin() as db:
        workflow = Workflow(prompt="Analyze sales", input_csv="item,sales\nA,10", status="running")
        db.add(workflow)
        db.flush()
        task = Task(
            workflow_id=workflow.id,
            name="inspect",
            agent="csv_inspector",
            capability="csv.inspect",
            ordinal=0,
            dependencies=[],
            input_bindings={},
        )
        db.add(task)
        db.flush()
        return workflow.id, task.id


@pytest.mark.parametrize(
    "design,requeued", [("full", True), ("no_fencing", True), ("no_lease", False)]
)
def test_expiry_and_restart_respect_lease_ablation(
    migrated_database, monkeypatch, design, requeued
):
    _, sessions, _ = migrated_database
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "EXPERIMENT_DESIGN", design)
    _, tid = seed(sessions)
    with sessions.begin() as db:
        task = db.get(Task, tid)
        task.status = "running"
        task.lease_owner = "dead"
        task.lease_expires_at = now() - timedelta(seconds=1)
    assert worker.tick("survivor") is requeued
    with sessions() as db:
        assert db.get(Task, tid).status == ("pending" if requeued else "running")
    # Exercise startup separately; it must not rescue the naive baseline either.
    with sessions.begin() as db:
        db.get(Task, tid).status = "running"
    worker.recover_running_tasks()
    with sessions() as db:
        assert db.get(Task, tid).status == ("pending" if requeued else "running")


@pytest.mark.parametrize(
    "design,accepted", [("full", False), ("no_fencing", True), ("no_lease", False)]
)
def test_stale_publication_only_bypasses_fence_in_ablation(
    migrated_database, monkeypatch, design, accepted
):
    _, sessions, _ = migrated_database
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker, "EXPERIMENT_DESIGN", design)
    _, tid = seed(sessions)
    original = worker.dispatch

    def superseded(request, **kwargs):
        result = original(request, **kwargs)
        with sessions.begin() as db:
            task = db.get(Task, tid)
            attempt = db.get(TaskAttempt, task.current_attempt_id)
            attempt.status = "lease_expired"
            task.current_attempt_id = "replacement-attempt"
            task.lease_owner = "replacement-worker"
        return result

    monkeypatch.setattr(worker, "dispatch", superseded)
    assert worker.tick("late-worker")
    with sessions() as db:
        task = db.get(Task, tid)
        assert (task.status == "succeeded") is accepted
        attempt = db.scalar(select(TaskAttempt).where(TaskAttempt.task_id == tid))
        assert (attempt.result_envelope is not None) is accepted
