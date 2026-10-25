"""Migration helpers and explicit adoption of an existing Checkpoint 01 database."""

import argparse
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from . import models  # noqa: F401
from .db import Base, engine


def migration_config(database_url: str | None = None) -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    if database_url is not None:
        config.attributes["database_url"] = database_url
    return config


def require_current_schema(db_engine: Engine = engine) -> None:
    with db_engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_heads()
    expected = ScriptDirectory.from_config(migration_config()).get_heads()
    if set(current) != set(expected):
        raise RuntimeError(
            "Database migrations are required. Run alembic upgrade head before starting services."
        )


def adopt_existing(db_engine: Engine = engine) -> None:
    with db_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        if context.get_current_heads():
            raise RuntimeError("Database is already versioned; use alembic upgrade head.")
        if compare_metadata(context, Base.metadata):
            raise RuntimeError("Existing schema does not match Checkpoint 01; adoption refused.")
    # Pin adoption to the baseline, even after later migrations are introduced.
    command.stamp(migration_config(db_engine.url.render_as_string(hide_password=False)), "0001")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adopt-existing", action="store_true", required=True)
    parser.parse_args()
    adopt_existing()


if __name__ == "__main__":
    main()
