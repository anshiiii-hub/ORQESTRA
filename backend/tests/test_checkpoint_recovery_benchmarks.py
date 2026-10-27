"""Tests for durable checkpoint recovery, pause/resume state machine, deep health checks, and benchmarks (PRD Section 8.4, 9.3, 14, 15, 19)."""

from alembic.script import ScriptDirectory
from sqlalchemy import select

from app.models import BenchmarkSet, Event, Task, TaskAttempt, Workflow
from app.worker import tick


def test_healthz_and_readyz_probes(api_client, accounts):
    # Liveness check
    resp = api_client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "orqestra-api"

    # Readiness check
    ready_resp = api_client.get("/readyz")
    assert ready_resp.status_code == 200
    ready_data = ready_resp.json()
    assert ready_data["status"] == "ready"
    assert ready_data["database"] == "connected"
    assert ready_data["schema_revision"] == "0014"


def test_readyz_rejects_outdated_database_schema(api_client, accounts, migrated_database):
    from sqlalchemy import text

    _, sessions, config = migrated_database
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    with sessions() as db:
        db.execute(text("UPDATE alembic_version SET version_num = '0012'"))
        db.commit()

    try:
        response = api_client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["detail"] == "Database schema is not current"
    finally:
        # Restore the real schema marker so migrated_database can safely downgrade.
        with sessions() as db:
            db.execute(
                text("UPDATE alembic_version SET version_num = :revision"),
                {"revision": head_revision},
            )
            db.commit()


def test_workflow_pause_and_resume_state_machine(api_client, accounts, migrated_database):
    _, sessions, _ = migrated_database
    owner = accounts("rec-owner@example.com")
    ws_resp = owner.post("/api/v1/workspaces", json={"name": "Recovery WS"})
    assert ws_resp.status_code == 201
    ws_id = ws_resp.json()["id"]

    # 1. Create workflow
    wf_resp = owner.post(
        "/api/v1/workflows",
        json={
            "workspace_id": ws_id,
            "prompt": "Summarize product performance and inspect columns",
            "csv_text": "product,sales,units\nWidget,1200,10\nGadget,2500,25",
            "planner_mode": "offline",
        },
    )
    assert wf_resp.status_code == 202
    wf_id = wf_resp.json()["workflow_id"]

    # 2. Inspect initial checkpoint
    chk_resp = owner.get(f"/api/v1/workflows/{wf_id}/checkpoint")
    assert chk_resp.status_code == 200
    chk_data = chk_resp.json()
    assert chk_data["status"] == "queued"
    assert chk_data["can_pause"] is True
    assert chk_data["can_resume"] is False

    # 3. Pause workflow
    pause_resp = owner.post(f"/api/v1/workflows/{wf_id}/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"

    # Check updated checkpoint state
    chk_resp2 = owner.get(f"/api/v1/workflows/{wf_id}/checkpoint")
    assert chk_resp2.status_code == 200
    chk_data2 = chk_resp2.json()
    assert chk_data2["status"] == "paused"
    assert chk_data2["can_pause"] is False
    assert chk_data2["can_resume"] is True

    # 4. Resume workflow
    resume_resp = owner.post(f"/api/v1/workflows/{wf_id}/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "queued"
    assert resume_resp.json()["checkpoint"]["resumed_from_checkpoint"] is True

    # Check events emitted
    with sessions() as db:
        events = list(db.scalars(select(Event).where(Event.workflow_id == wf_id)).all())
        event_kinds = [e.kind for e in events]
        assert "workflow_paused" in event_kinds
        assert "workflow_resumed" in event_kinds


def test_checkpoint_boundary_preservation_and_retry(api_client, accounts, migrated_database):
    _, sessions, _ = migrated_database
    owner = accounts("retry-owner@example.com")
    ws_resp = owner.post("/api/v1/workspaces", json={"name": "Retry WS"})
    assert ws_resp.status_code == 201
    ws_id = ws_resp.json()["id"]

    # 1. Create workflow
    wf_resp = owner.post(
        "/api/v1/workflows",
        json={
            "workspace_id": ws_id,
            "prompt": "Multi-stage data inspection",
            "csv_text": "category,val\nA,1\nB,2",
            "planner_mode": "offline",
        },
    )
    assert wf_resp.status_code == 202
    wf_id = wf_resp.json()["workflow_id"]

    # 2. Simulate task 1 succeeded and task 2 failed
    with sessions() as db:
        wf = db.get(Workflow, wf_id)
        assert wf is not None
        tasks = list(
            db.scalars(select(Task).where(Task.workflow_id == wf_id).order_by(Task.ordinal)).all()
        )
        assert len(tasks) >= 2

        tasks[0].status = "succeeded"
        tasks[0].result = {"summary": "Task 1 completed successfully"}

        tasks[1].status = "failed"
        tasks[1].error = "Simulated worker fault"

        wf.status = "failed"
        wf.error = "Simulated worker fault"
        db.commit()

    # 3. Retry workflow with preserve_checkpoint=True
    retry_resp = owner.post(
        f"/api/v1/workflows/{wf_id}/retry",
        json={"preserve_checkpoint": True},
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json()["status"] == "queued"

    # 4. Verify Task 1 was preserved and Task 2 was reset to pending
    with sessions() as db:
        wf = db.get(Workflow, wf_id)
        assert wf is not None
        assert wf.status == "queued"
        assert wf.error is None

        tasks = list(
            db.scalars(select(Task).where(Task.workflow_id == wf_id).order_by(Task.ordinal)).all()
        )
        assert tasks[0].status == "succeeded"
        assert tasks[0].result == {"summary": "Task 1 completed successfully"}

        assert tasks[1].status == "pending"
        assert tasks[1].error is None


def test_demo_d_fault_tolerance_resilience_simulation(api_client, accounts, migrated_database):
    """PRD Section 20 Demo D: Mid-flight pause/worker recovery preserves completed nodes and finishes remaining nodes."""
    _, sessions, _ = migrated_database
    owner = accounts("demo-d@example.com")
    ws_resp = owner.post("/api/v1/workspaces", json={"name": "Demo D WS"})
    assert ws_resp.status_code == 201
    ws_id = ws_resp.json()["id"]

    wf_resp = owner.post(
        "/api/v1/workflows",
        json={
            "workspace_id": ws_id,
            "prompt": "Inspect and summarize sales numbers",
            "csv_text": "product,sales,units\nFrames,2400,12\nSunglasses,1800,9\nLenses,1200,20",
            "planner_mode": "offline",
        },
    )
    wf_id = wf_resp.json()["workflow_id"]

    # Execute first task
    claimed = tick()
    assert claimed is True

    # Verify first task succeeded and second task is pending
    with sessions() as db:
        tasks = list(
            db.scalars(select(Task).where(Task.workflow_id == wf_id).order_by(Task.ordinal)).all()
        )
        assert tasks[0].status == "succeeded"
        first_task_result = tasks[0].result

    # Pause workflow mid-flight
    pause_resp = owner.post(f"/api/v1/workflows/{wf_id}/pause")
    assert pause_resp.status_code == 200

    # Worker executes: while paused, no tasks are picked up
    assert tick() is False

    # Resume workflow
    resume_resp = owner.post(f"/api/v1/workflows/{wf_id}/resume")
    assert resume_resp.status_code == 200

    # Execute remaining tasks until workflow completes
    for _ in range(10):
        if not tick():
            break

    # Verify entire workflow succeeded and task 0 result was preserved
    with sessions() as db:
        wf = db.get(Workflow, wf_id)
        assert wf is not None
        assert wf.status == "succeeded"
        assert wf.output is not None

        tasks = list(
            db.scalars(select(Task).where(Task.workflow_id == wf_id).order_by(Task.ordinal)).all()
        )
        assert all(t.status == "succeeded" for t in tasks)
        assert tasks[0].result == first_task_result


def test_pause_during_dispatch_requeues_inflight_task(
    api_client, accounts, migrated_database, monkeypatch
):
    from app import worker as worker_module
    from app.recovery import pause_workflow

    _, sessions, _ = migrated_database
    owner = accounts("pause-race@example.com")
    workspace_id = owner.post("/api/v1/workspaces", json={"name": "Pause race"}).json()["id"]
    workflow_id = owner.post(
        "/api/v1/workflows",
        json={
            "workspace_id": workspace_id,
            "prompt": "Check pause during a running task",
            "csv_text": "item,value\nA,1\nB,2",
            "planner_mode": "offline",
        },
    ).json()["workflow_id"]
    original_dispatch = worker_module.dispatch

    def pause_before_dispatch_result(request, *args, **kwargs):
        with sessions() as db:
            pause_workflow(db, request.workflow_id)
        return original_dispatch(request, *args, **kwargs)

    monkeypatch.setattr(worker_module, "dispatch", pause_before_dispatch_result)
    assert worker_module.tick() is True

    with sessions() as db:
        workflow = db.get(Workflow, workflow_id)
        tasks = list(
            db.scalars(select(Task).where(Task.workflow_id == workflow_id).order_by(Task.ordinal))
        )
        attempts = list(
            db.scalars(select(TaskAttempt).where(TaskAttempt.workflow_id == workflow_id)).all()
        )
        assert workflow is not None and workflow.status == "paused"
        assert tasks[0].status == "pending"
        assert attempts[0].status == "superseded"

    assert owner.post(f"/api/v1/workflows/{workflow_id}/retry").status_code == 400
    assert owner.post(f"/api/v1/workflows/{workflow_id}/resume").status_code == 200
    monkeypatch.setattr(worker_module, "dispatch", original_dispatch)
    for _ in range(10):
        if not worker_module.tick():
            break
    with sessions() as db:
        workflow = db.get(Workflow, workflow_id)
        tasks = list(
            db.scalars(select(Task).where(Task.workflow_id == workflow_id).order_by(Task.ordinal))
        )
        assert workflow is not None and workflow.status == "succeeded"
        assert all(task.status == "succeeded" for task in tasks)


def test_benchmark_suite_execution_and_summary(api_client, accounts, migrated_database):
    _, sessions, _ = migrated_database
    owner = accounts("bench-runner@example.com")
    workspace_id = owner.post("/api/v1/workspaces", json={"name": "Benchmark WS"}).json()["id"]

    # Reading the matrix must not seed data or execute benchmark cases.
    empty_summary = owner.get(f"/api/v1/benchmarks/summary?workspace_id={workspace_id}").json()
    assert empty_summary["total_executions"] == 0
    with sessions() as db:
        assert db.query(BenchmarkSet).count() == 0

    execution_ids = {}
    for mode in ("single", "fixed", "adaptive"):
        exec_resp = owner.post(
            "/api/v1/benchmarks/executions",
            json={"workspace_id": workspace_id, "routing_mode": mode, "replicates": 3},
        )
        assert exec_resp.status_code == 200, exec_resp.text
        data = exec_resp.json()
        assert data["status"] == "completed"
        assert data["count"] == 78 * 3
        results = data["results"]
        assert all(r["routing_mode"] == mode for r in results)
        assert all(r["duration_ms"] > 0 for r in results)
        assert all(
            r["metadata"]["execution_kind"] == "in_process_deterministic_dispatch" for r in results
        )
        assert all(r["metadata"]["expected_output_matched"] for r in results)
        assert {r["metadata"]["replicate_number"] for r in results} == {1, 2, 3}
        execution_ids[mode] = results[0]["execution_id"]

    # Execution details are also workspace-scoped.
    by_exec_resp = owner.get(
        f"/api/v1/benchmarks/executions/{execution_ids['single']}?workspace_id={workspace_id}"
    )
    assert by_exec_resp.status_code == 200
    assert len(by_exec_resp.json()["results"]) == 78 * 3

    # Summary reports the frozen dataset and all three baselines.
    summary_resp = owner.get(f"/api/v1/benchmarks/summary?workspace_id={workspace_id}")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert "baselines" in summary
    assert "fixed" in summary["baselines"]
    assert "adaptive" in summary["baselines"]
    assert "single" in summary["baselines"]
    assert summary["case_count"] == 78
    assert len(summary["dataset_sha256"]) == 64
    assert summary["baselines"]["fixed"]["runs"] == 78 * 3
    assert summary["baselines"]["adaptive"]["runs"] == 78 * 3
    assert summary["baselines"]["single"]["runs"] == 78 * 3
    assert summary["total_executions"] == 3
    assert summary["parallel_speedup"] is None
    assert "recovery_metrics" not in summary


def test_benchmark_fixture_is_frozen_and_injection_case_is_data():
    from app.benchmarks import load_fixture

    dataset, digest = load_fixture()
    cases = dataset["cases"]
    assert len(cases) == 78
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert sum(case["expected"]["status"] == "succeeded" for case in cases) == 71
    assert sum(case["expected"]["status"] == "failed" for case in cases) == 7
    assert len(digest) == 64
    injection_cases = [case for case in cases if "quoted-untrusted-text" in case["category"]]
    assert injection_cases
    assert any("ignore previous instructions" in case["csv"] for case in injection_cases)


def test_benchmark_workspace_isolation_and_rbac(api_client, accounts):
    owner_a = accounts("benchmark-iso-a@example.com")
    owner_b = accounts("benchmark-iso-b@example.com")
    ws_a = owner_a.post("/api/v1/workspaces", json={"name": "Benchmark A"}).json()["id"]
    ws_b = owner_b.post("/api/v1/workspaces", json={"name": "Benchmark B"}).json()["id"]
    response = owner_a.post(
        "/api/v1/benchmarks/executions",
        json={"workspace_id": ws_a, "routing_mode": "single", "replicates": 3},
    )
    assert response.status_code == 200
    execution_id = response.json()["results"][0]["execution_id"]

    assert (
        owner_b.post(
            "/api/v1/benchmarks/executions",
            json={"workspace_id": ws_a, "routing_mode": "single", "replicates": 3},
        ).status_code
        == 404
    )
    other_summary = owner_b.get(f"/api/v1/benchmarks/summary?workspace_id={ws_b}").json()
    assert other_summary["total_executions"] == 0
    assert (
        owner_b.get(f"/api/v1/benchmarks/executions/{execution_id}?workspace_id={ws_b}").status_code
        == 404
    )


def test_metrics_overview_endpoint(api_client, accounts):
    owner = accounts("metrics-user@example.com")
    ws_resp = owner.post("/api/v1/workspaces", json={"name": "Metrics WS"})
    assert ws_resp.status_code == 201
    ws_id = ws_resp.json()["id"]

    # Create and pause a workflow to record recovery metrics
    wf_resp = owner.post(
        "/api/v1/workflows",
        json={
            "workspace_id": ws_id,
            "prompt": "Metrics sample run",
            "csv_text": "a,b\n1,2",
            "planner_mode": "offline",
        },
    )
    wf_id = wf_resp.json()["workflow_id"]
    owner.post(f"/api/v1/workflows/{wf_id}/pause")
    owner.post(f"/api/v1/workflows/{wf_id}/resume")

    # Fetch metrics overview
    metrics_resp = owner.get(f"/api/v1/metrics/overview?workspace_id={ws_id}")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert metrics["workspace_id"] == ws_id
    assert metrics["total_workflows"] >= 1
    assert "status_counts" in metrics
    assert "recovery_metrics" in metrics
    assert metrics["recovery_metrics"]["resumed_from_checkpoint"] >= 1
    assert metrics["recovery_metrics"]["eligible_workflows"] == 1
    assert metrics["recovery_metrics"]["recovery_rate"] == 1.0


def test_recovery_workspace_isolation_and_rbac(api_client, accounts):
    owner_a = accounts("iso-a@example.com")
    owner_b = accounts("iso-b@example.com")

    ws_a = owner_a.post("/api/v1/workspaces", json={"name": "Isolation WS A"}).json()["id"]
    ws_b = owner_b.post("/api/v1/workspaces", json={"name": "Isolation WS B"}).json()["id"]

    # User B adds a viewer
    viewer_b = accounts("viewer-b@example.com")
    owner_b.post(
        f"/api/v1/workspaces/{ws_b}/members",
        json={"email": "viewer-b@example.com", "role": "viewer"},
    )

    # Workflow created in WS A
    wf_a = owner_a.post(
        "/api/v1/workflows",
        json={
            "workspace_id": ws_a,
            "prompt": "Private analysis",
            "csv_text": "x,y\n1,2",
            "planner_mode": "offline",
        },
    ).json()["workflow_id"]

    # Owner B cannot pause Workflow A (404 isolation)
    cross_resp = owner_b.post(f"/api/v1/workflows/{wf_a}/pause")
    assert cross_resp.status_code == 404

    # Workflow in WS B
    wf_b = owner_b.post(
        "/api/v1/workflows",
        json={
            "workspace_id": ws_b,
            "prompt": "WS B workflow",
            "csv_text": "x,y\n1,2",
            "planner_mode": "offline",
        },
    ).json()["workflow_id"]

    # Viewer B can inspect checkpoint (200)
    chk_resp = viewer_b.get(f"/api/v1/workflows/{wf_b}/checkpoint")
    assert chk_resp.status_code == 200

    # Viewer B CANNOT pause workflow (403 forbidden)
    pause_resp = viewer_b.post(f"/api/v1/workflows/{wf_b}/pause")
    assert pause_resp.status_code == 403
