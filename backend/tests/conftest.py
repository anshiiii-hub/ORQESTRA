import os

import pytest
from alembic import command
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.migrate import migration_config


@pytest.fixture
def migrated_database(tmp_path):
    # TEST_DATABASE_URL must name a disposable database: this fixture drops its schema.
    url = os.getenv("TEST_DATABASE_URL", f"sqlite:///{tmp_path / 'integration.db'}")
    config = migration_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield engine, sessions, config
    finally:
        engine.dispose()
        command.downgrade(config, "base")


@pytest.fixture
def api_client(migrated_database, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main, worker
    from app.migrate import require_current_schema

    engine, sessions, _ = migrated_database
    monkeypatch.setattr(main, "require_current_schema", lambda: require_current_schema(engine))
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(main, "next_db", sessions)

    def get_test_db():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[main.get_db] = get_test_db
    try:
        with TestClient(main.app, headers={"Origin": "http://localhost:5173"}) as client:
            yield client
    finally:
        main.app.dependency_overrides.clear()


@pytest.fixture
def accounts(api_client):
    from fastapi.testclient import TestClient

    from app.main import app

    clients = []

    def create(email):
        client = TestClient(app, headers={"Origin": "http://localhost:5173"})
        clients.append(client)
        response = client.post(
            "/api/v1/auth/register", json={"email": email, "password": "correct-horse-battery"}
        )
        assert response.status_code == 201, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return client

    yield create
    for client in clients:
        client.close()
