"""Persist agent contract versions, input bindings, envelopes and worker heartbeats."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tasks", sa.Column("capability", sa.String(64), nullable=False, server_default="")
    )
    op.add_column(
        "tasks", sa.Column("agent_version", sa.String(16), nullable=False, server_default="1.0.0")
    )
    op.add_column(
        "tasks", sa.Column("schema_version", sa.String(16), nullable=False, server_default="1.0")
    )
    op.add_column(
        "tasks",
        sa.Column("input_bindings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("tasks", sa.Column("request_envelope", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("result_envelope", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("error_code", sa.String(64), nullable=True))
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.String),
        sa.column("workflow_id", sa.String),
        sa.column("name", sa.String),
        sa.column("agent", sa.String),
        sa.column("capability", sa.String),
        sa.column("dependencies", sa.JSON),
        sa.column("input_bindings", sa.JSON),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(tasks)).mappings().all()
    workflows = {}
    for row in rows:
        workflows.setdefault(row["workflow_id"], set()).add(row["name"])
    definitions = {
        "csv_inspector": ("csv.inspect", ()),
        "csv_summarizer": ("csv.summarize", ("inspect",)),
        "result_verifier": ("result.verify", ("inspect", "summarize")),
    }
    for row in rows:
        if row["agent"] not in definitions:
            continue
        capability, required = definitions[row["agent"]]
        bindings = {name: name for name in required if name in workflows[row["workflow_id"]]}
        dependencies = list(dict.fromkeys([*row["dependencies"], *bindings.values()]))
        connection.execute(
            tasks.update()
            .where(tasks.c.id == row["id"])
            .values(capability=capability, input_bindings=bindings, dependencies=dependencies)
        )
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_versions", sa.JSON(), nullable=False),
    )
    op.create_index("ix_worker_heartbeats_seen_at", "worker_heartbeats", ["seen_at"])


def downgrade():
    op.drop_table("worker_heartbeats")
    with op.batch_alter_table("tasks") as batch:
        for name in (
            "error_code",
            "result_envelope",
            "request_envelope",
            "input_bindings",
            "schema_version",
            "agent_version",
            "capability",
        ):
            batch.drop_column(name)
