import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app import main, worker
from app.db import Base
from app.legacy_schema import checkpoint_one_metadata
from app.migrate import adopt_existing, migration_config, require_current_schema
from app.models import Workflow, now


def test_migration_round_trip_matches_models(migrated_database):
    engine, _, config = migrated_database
    require_current_schema(engine)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    with pytest.raises(RuntimeError, match="migrations are required"):
        require_current_schema(engine)
    command.upgrade(config, "head")
    require_current_schema(engine)


def test_adoption_preserves_existing_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    try:
        legacy = checkpoint_one_metadata()
        legacy.create_all(engine)
        from sqlalchemy.orm import Session

        wid = "legacy-workflow"
        with engine.begin() as connection:
            connection.execute(
                legacy.tables["workflows"]
                .insert()
                .values(
                    id=wid,
                    prompt="Keep existing data",
                    status="succeeded",
                    mode="csv_demo",
                    input_csv="a\n1",
                    created_at=now(),
                    updated_at=now(),
                )
            )
        adopt_existing(engine)
        command.upgrade(migration_config(str(engine.url)), "head")
        require_current_schema(engine)
        with Session(engine) as db:
            assert db.get(Workflow, wid).prompt == "Keep existing data"
            assert db.get(Workflow, wid).workspace_id is None
        with pytest.raises(RuntimeError, match="already versioned"):
            adopt_existing(engine)
    finally:
        engine.dispose()


def test_adoption_rejects_incompatible_schema(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'invalid.db'}")
    try:
        with pytest.raises(RuntimeError, match="adoption refused"):
            adopt_existing(engine)
    finally:
        engine.dispose()


def test_authenticated_workflow_runs_on_migrated_database(migrated_database, monkeypatch):
    engine, sessions, _ = migrated_database
    monkeypatch.setattr(main, "require_current_schema", lambda: require_current_schema(engine))
    monkeypatch.setattr(worker, "SessionLocal", sessions)

    def get_test_db():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[main.get_db] = get_test_db
    headers = {"Origin": "http://localhost:5173"}
    try:
        with TestClient(main.app) as client:
            assert client.get("/api/v1/workflows").status_code == 401
            registered = client.post(
                "/api/v1/auth/register",
                headers=headers,
                json={"email": "integration@example.com", "password": "integration-password"},
            )
            assert registered.status_code == 201
            headers["X-CSRF-Token"] = registered.json()["csrf_token"]
            workspace = client.post(
                "/api/v1/workspaces", headers=headers, json={"name": "Integration"}
            )
            assert workspace.status_code == 201
            response = client.post(
                "/api/v1/workflows",
                headers=headers,
                json={
                    "workspace_id": workspace.json()["id"],
                    "prompt": "Analyze sales",
                    "csv_text": "item,sales\nA,10\nB,20",
                },
            )
            assert response.status_code == 202
            wid = response.json()["workflow_id"]
            for _ in range(4):
                worker.tick()
            result = client.get(f"/api/v1/workflows/{wid}", headers=headers).json()
            assert result["status"] == "succeeded"
            assert result["output"]["summary"]["numeric_columns"]["sales"]["sum"] == 30
            events = client.get(f"/api/v1/workflows/{wid}/events", headers=headers).json()
            assert events[-1]["kind"] == "workflow_succeeded"
    finally:
        main.app.dependency_overrides.clear()


def test_contract_migration_backfills_and_resumes_existing_queue(migrated_database, monkeypatch):
    from sqlalchemy import MetaData, Table

    from app.models import Task

    engine, sessions, config = migrated_database
    command.downgrade(config, "0002")
    old_tasks = Table("tasks", MetaData(), autoload_with=engine)
    old_workflows = Table("workflows", MetaData(), autoload_with=engine)
    wid = "pre-contract-workflow"
    with sessions.begin() as db:
        db.execute(
            old_workflows.insert().values(
                id=wid,
                prompt="Existing queued workflow",
                input_csv="item,sales\nA,10\nB,20",
                status="queued",
                mode="csv_demo",
                created_at=now(),
                updated_at=now(),
            )
        )
        for index, (name, agent, dependencies) in enumerate(
            [
                ("inspect", "csv_inspector", []),
                ("summarize", "csv_summarizer", ["inspect"]),
                ("verify", "result_verifier", ["summarize"]),
            ]
        ):
            db.execute(
                old_tasks.insert().values(
                    id=f"old-task-{index}",
                    workflow_id=wid,
                    name=name,
                    agent=agent,
                    dependencies=dependencies,
                    status="pending",
                    ordinal=index,
                    attempts=0,
                )
            )
    command.upgrade(config, "head")
    with sessions() as db:
        verifier = db.get(Task, "old-task-2")
        assert verifier.capability == "result.verify"
        assert verifier.input_bindings == {"inspect": "inspect", "summarize": "summarize"}
        assert set(verifier.dependencies) == {"inspect", "summarize"}
        assert verifier.result_envelope is None
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    for _ in range(4):
        worker.tick()
    with sessions() as db:
        assert db.get(Workflow, wid).status == "succeeded"
        assert db.get(Workflow, wid).output["summary"]["numeric_columns"]["sales"]["sum"] == 30
