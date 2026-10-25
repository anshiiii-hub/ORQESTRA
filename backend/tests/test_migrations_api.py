import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app import main, worker
from app.db import Base
from app.migrate import adopt_existing, require_current_schema
from app.models import Workflow


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
        Base.metadata.create_all(engine)
        from sqlalchemy.orm import Session

        with Session(engine) as db:
            workflow = Workflow(prompt="Keep existing data")
            db.add(workflow)
            db.commit()
            wid = workflow.id
        adopt_existing(engine)
        require_current_schema(engine)
        with Session(engine) as db:
            assert db.get(Workflow, wid).prompt == "Keep existing data"
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
    monkeypatch.setenv("API_KEY", "integration-only-key")
    monkeypatch.setattr(main, "require_current_schema", lambda: require_current_schema(engine))
    monkeypatch.setattr(worker, "SessionLocal", sessions)

    def get_test_db():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[main.get_db] = get_test_db
    headers = {"X-API-Key": "integration-only-key"}
    try:
        with TestClient(main.app) as client:
            assert client.get("/api/v1/workflows").status_code == 401
            response = client.post(
                "/api/v1/workflows",
                headers=headers,
                json={"prompt": "Analyze sales", "csv_text": "item,sales\nA,10\nB,20"},
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
