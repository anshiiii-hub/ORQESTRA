"""Exercise real worker process termination against a disposable PostgreSQL database."""

import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app import worker
from app.models import Task, TaskAttempt, Workflow, now


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="worker process recovery acceptance requires disposable PostgreSQL",
)
@pytest.mark.parametrize("cancelled", [False, True])
def test_killed_worker_preserves_completed_tasks_and_recovers_unfinished(
    accounts, migrated_database, tmp_path, cancelled
):
    client = accounts("process-recovery@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "Process recovery"}).json()["id"]
    response = client.post(
        "/api/v1/workflows",
        json={
            "workspace_id": workspace,
            "prompt": "Analyze sales",
            "csv_text": "item,sales\nA,10\nB,20",
        },
    )
    workflow_id = response.json()["workflow_id"]
    _, sessions, _ = migrated_database
    for _ in range(5):
        assert worker.tick("completed-worker")
        with sessions() as db:
            first = db.scalar(
                select(Task).where(Task.workflow_id == workflow_id, Task.ordinal == 0)
            )
            if first is not None and first.status == "succeeded":
                break
    with sessions() as db:
        completed = db.scalar(
            select(Task).where(Task.workflow_id == workflow_id, Task.ordinal == 0)
        )
        assert completed.status == "succeeded"
        completed_id, completed_result = completed.id, completed.result
    marker = tmp_path / "claimed"
    env = {
        **os.environ,
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "DATABASE_PASSWORD_FILE": "",
        "PGOPTIONS": "-c timezone=Asia/Kolkata",
    }
    code = """
import time
from pathlib import Path
from app import worker
original = worker.dispatch
def blocked(request, **kwargs):
    Path(__import__('sys').argv[1]).write_text('claimed')
    time.sleep(60)
    return original(request, **kwargs)
worker.dispatch = blocked
worker.tick('terminated-worker')
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(marker)], env=env, cwd=Path(__file__).resolve().parents[1]
    )
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "child did not durably claim a task"
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    if cancelled:
        assert client.post(f"/api/v1/workflows/{workflow_id}/cancel").status_code == 200
    with sessions.begin() as db:
        task = db.scalar(select(Task).where(Task.workflow_id == workflow_id, Task.ordinal == 1))
        task.lease_expires_at = now() - timedelta(seconds=1)
        for attempt in db.scalars(select(TaskAttempt).where(TaskAttempt.task_id == task.id)):
            attempt.lease_expires_at = now() - timedelta(seconds=1)
    restart = "from app import worker; worker.recover_running_tasks(); [worker.tick('restarted-worker') for _ in range(5)]"
    subprocess.run(
        [sys.executable, "-c", restart],
        env=env,
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        timeout=30,
    )
    with sessions() as db:
        workflow = db.get(Workflow, workflow_id)
        preserved = db.get(Task, completed_id)
        assert preserved.result == completed_result
        assert preserved.attempts == 1
        assert workflow.status == ("cancelled" if cancelled else "succeeded"), [
            (t.name, t.ordinal, t.status, str(t.lease_expires_at), t.error)
            for t in db.scalars(select(Task).where(Task.workflow_id == workflow_id))
        ]
        if not cancelled:
            assert workflow.output["summary"]["numeric_columns"]["sales"]["sum"] == 30
