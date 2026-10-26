"""Add workflow recovery metadata, benchmark sets, cases, and evaluation results."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflows", sa.Column("recovery_metadata", sa.JSON(), nullable=True))

    op.create_table(
        "benchmark_sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "benchmark_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "set_id",
            sa.String(36),
            sa.ForeignKey("benchmark_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_key", sa.String(100), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("expected_output_pattern", sa.String(255), nullable=False),
        sa.Column("routing_mode", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_benchmark_cases_set_id", "benchmark_cases", ["set_id"])
    op.create_index("ix_benchmark_cases_case_key", "benchmark_cases", ["case_key"])

    op.create_table(
        "benchmark_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "benchmark_case_id",
            sa.String(36),
            sa.ForeignKey("benchmark_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("routing_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("benchmark_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_benchmark_results_benchmark_case_id", "benchmark_results", ["benchmark_case_id"]
    )
    op.create_index("ix_benchmark_results_workflow_id", "benchmark_results", ["workflow_id"])
    op.create_index("ix_benchmark_results_execution_id", "benchmark_results", ["execution_id"])


def downgrade():
    op.drop_index("ix_benchmark_results_execution_id", table_name="benchmark_results")
    op.drop_index("ix_benchmark_results_workflow_id", table_name="benchmark_results")
    op.drop_index("ix_benchmark_results_benchmark_case_id", table_name="benchmark_results")
    op.drop_table("benchmark_results")

    op.drop_index("ix_benchmark_cases_case_key", table_name="benchmark_cases")
    op.drop_index("ix_benchmark_cases_set_id", table_name="benchmark_cases")
    op.drop_table("benchmark_cases")

    op.drop_table("benchmark_sets")

    op.drop_column("workflows", "recovery_metadata")
