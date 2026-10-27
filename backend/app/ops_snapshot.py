"""Emit a small non-secret database snapshot for backup/restore verification."""

import json

from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select

from .db import engine
from .models import Artifact, Task, User, Workflow, Workspace


def snapshot() -> dict[str, object]:
    models = {
        "users": User,
        "workspaces": Workspace,
        "workflows": Workflow,
        "tasks": Task,
        "artifacts": Artifact,
    }
    with engine.connect() as connection:
        revision = sorted(MigrationContext.configure(connection).get_current_heads())
        counts = {
            name: int(connection.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in models.items()
        }
    return {"schema_revisions": revision, "counts": counts}


if __name__ == "__main__":
    print(json.dumps(snapshot(), sort_keys=True))
