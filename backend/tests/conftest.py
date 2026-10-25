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
