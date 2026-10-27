"""Freeze the evaluation dataset and scope benchmark results to workspaces."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("benchmark_sets", sa.Column("dataset_sha256", sa.String(64), nullable=True))
    with op.batch_alter_table("benchmark_results") as batch:
        batch.add_column(
            sa.Column(
                "workspace_id",
                sa.String(36),
                sa.ForeignKey(
                    "workspaces.id",
                    ondelete="CASCADE",
                    name="fk_benchmark_results_workspace_id_workspaces",
                ),
                nullable=True,
            )
        )
    op.create_index("ix_benchmark_results_workspace_id", "benchmark_results", ["workspace_id"])
    op.add_column(
        "benchmark_results",
        sa.Column("replicate_number", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade():
    op.drop_column("benchmark_results", "replicate_number")
    op.drop_index("ix_benchmark_results_workspace_id", table_name="benchmark_results")
    with op.batch_alter_table("benchmark_results") as batch:
        batch.drop_column("workspace_id")
    op.drop_column("benchmark_sets", "dataset_sha256")
