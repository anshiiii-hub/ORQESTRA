"""Persist workspace-scoped research runs and untrusted source records."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("query", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_research_runs_workspace_id", "research_runs", ["workspace_id"])
    op.create_index("ix_research_runs_status", "research_runs", ["status"])
    op.create_table(
        "research_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trust_level", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.UniqueConstraint("run_id", "url", name="uq_research_source_run_url"),
        sa.UniqueConstraint("run_id", "position", name="uq_research_source_run_position"),
        sa.CheckConstraint("trust_level = 'untrusted'", name="ck_research_source_untrusted"),
    )
    op.create_index("ix_research_sources_run_id", "research_sources", ["run_id"])


def downgrade():
    op.drop_table("research_sources")
    op.drop_table("research_runs")
