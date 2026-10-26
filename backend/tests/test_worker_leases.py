import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import select

from app import worker
from app.models import Task, TaskAttempt, Workflow, now
from app.registry import AgentRegistry, build_registry

POSTGRES_TEST = os.getenv("TEST_DATABASE_URL", "").startswith("postgresql")


@pytest.mark.skipif(not POSTGRES_TEST, reason="concurrent row-lock coverage requires PostgreSQL")
def test_two_workers_execute_independent_ready_nodes_concurrently(
    accounts, migrated_database, monkeypatch
):
    client = accounts("parallel-workers@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "Parallel worker tests"}).json()[
        "id"
    ]
    _, sessions, _ = migrated_database
    with sessions.begin() as db:
        workflow = Workflow(
            workspace_id=workspace,
            prompt="Inspect both files independently",
            input_csv="item,sales\nA,10",
            status="queued",
        )
        db.add(workflow)
        db.flush()
        first = Task(
            workflow_id=workflow.id,
            name="inspect_a",
            agent="csv_inspector",
            capability="csv.inspect",
            dependencies=[],
            input_bindings={},
            ordinal=0,
        )
        second = Task(
            workflow_id=workflow.id,
            name="inspect_b",
            agent="csv_inspector",
            capability="csv.inspect",
            dependencies=[],
            input_bindings={},
            ordinal=1,
        )
        db.add_all([first, second])

    rendezvous = Barrier(2)
    original = build_registry().select("csv.inspect").handler

    def concurrent_inspection(csv_text, context):
        rendezvous.wait(timeout=10)
        return original(csv_text, context)

    definitions = build_registry().all()
    monkeypatch.setattr(
        worker,
        "REGISTRY",
        AgentRegistry(
            [
                replace(agent, handler=concurrent_inspection)
                if agent.capability == "csv.inspect"
                else agent
                for agent in definitions
            ]
        ),
    )

    def claim_and_run(worker_id):
        for _ in range(100):
            claimed = worker.tick(worker_id)
            if claimed:
                return True
            time.sleep(0.02)
        return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim_and_run, ["worker-a", "worker-b"]))

    assert outcomes == [True, True]
    with sessions() as db:
        tasks = db.scalars(
            select(Task).where(Task.workflow_id == workflow.id).order_by(Task.ordinal)
        ).all()
        attempts = db.scalars(
            select(TaskAttempt).where(TaskAttempt.workflow_id == workflow.id)
        ).all()
        assert [task.status for task in tasks] == ["succeeded", "succeeded"]
        assert len(attempts) == 2
        assert {attempt.worker_id for attempt in attempts} == {"worker-a", "worker-b"}
        assert all(attempt.status == "succeeded" for attempt in attempts)


@pytest.mark.skipif(not POSTGRES_TEST, reason="concurrent row-lock coverage requires PostgreSQL")
def test_one_worker_does_not_claim_another_workers_unexpired_task(
    accounts, migrated_database, monkeypatch
):
    client = accounts("leased-worker@example.com")
    workspace = client.post("/api/v1/workspaces", json={"name": "Lease tests"}).json()["id"]
    _, sessions, _ = migrated_database
    with sessions.begin() as db:
        workflow = Workflow(
            workspace_id=workspace,
            prompt="Wait for lease",
            input_csv="item,sales\nA,10",
            status="running",
        )
        db.add(workflow)
        db.flush()
        db.add(
            Task(
                workflow_id=workflow.id,
                name="in_flight",
                agent="csv_inspector",
                capability="csv.inspect",
                status="running",
                attempts=1,
                current_attempt_id="active-attempt",
                lease_owner="worker-a",
                lease_expires_at=now() + timedelta(seconds=30),
                dependencies=[],
                input_bindings={},
                ordinal=0,
            )
        )
    assert worker.tick("worker-b") is False
    with sessions() as db:
        assert db.scalar(select(TaskAttempt).where(TaskAttempt.workflow_id == workflow.id)) is None
