"""Add durable task attempts and fenced worker leases."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tasks", sa.Column("current_attempt_id", sa.String(36), nullable=True))
    op.add_column("tasks", sa.Column("lease_owner", sa.String(36), nullable=True))
    op.add_column("tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tasks_current_attempt_id", "tasks", ["current_attempt_id"])
    op.create_index("ix_tasks_lease_owner", "tasks", ["lease_owner"])

    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False, unique=True),
        sa.Column("request_envelope", sa.JSON(), nullable=True),
        sa.Column("result_envelope", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_number > 0", name="ck_task_attempt_number_positive"),
        sa.UniqueConstraint("task_id", "attempt_number", name="uq_task_attempt_number"),
    )
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])
    op.create_index("ix_task_attempts_workflow_id", "task_attempts", ["workflow_id"])
    op.create_index("ix_task_attempts_worker_id", "task_attempts", ["worker_id"])
    op.create_index("ix_task_attempts_status", "task_attempts", ["status"])
    op.create_index("ix_task_attempts_lease_expires_at", "task_attempts", ["lease_expires_at"])

    op.create_table(
        "task_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "dependency_task_id",
            sa.String(36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.CheckConstraint("task_id <> dependency_task_id", name="ck_task_dependency_not_self"),
        sa.UniqueConstraint("task_id", "dependency_task_id", name="uq_task_dependency_edge"),
    )
    op.create_index("ix_task_dependencies_workflow_id", "task_dependencies", ["workflow_id"])
    op.create_index("ix_task_dependencies_task_id", "task_dependencies", ["task_id"])
    op.create_index(
        "ix_task_dependencies_dependency_task_id", "task_dependencies", ["dependency_task_id"]
    )

    # Preserve existing JSON dependency lists as normalized scheduler edges.
    connection = op.get_bind()
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.String()),
        sa.column("workflow_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("dependencies", sa.JSON()),
    )
    by_workflow = {}
    for row in connection.execute(sa.select(tasks)).mappings():
        by_workflow.setdefault(row["workflow_id"], {})[row["name"]] = row["id"]
    edges = sa.table(
        "task_dependencies",
        sa.column("workflow_id", sa.String()),
        sa.column("task_id", sa.String()),
        sa.column("dependency_task_id", sa.String()),
    )
    for row in connection.execute(sa.select(tasks)).mappings():
        names = by_workflow[row["workflow_id"]]
        for dependency_name in row["dependencies"] or []:
            if dependency_name not in names:
                raise RuntimeError("Cannot migrate a task with a missing dependency")
            connection.execute(
                sa.insert(edges).values(
                    workflow_id=row["workflow_id"],
                    task_id=row["id"],
                    dependency_task_id=names[dependency_name],
                )
            )


def downgrade():
    op.drop_table("task_dependencies")
    op.drop_table("task_attempts")
    op.drop_index("ix_tasks_lease_owner", table_name="tasks")
    op.drop_index("ix_tasks_current_attempt_id", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
        batch.drop_column("current_attempt_id")
