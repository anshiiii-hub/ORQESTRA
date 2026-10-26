"""Store workspace-scoped code proposals and sandbox run artifacts."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "code_sandbox_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("patch_json", sa.JSON(), nullable=False),
        sa.Column("artifact_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_code_sandbox_runs_workspace_id", "code_sandbox_runs", ["workspace_id"])
    op.create_index("ix_code_sandbox_runs_status", "code_sandbox_runs", ["status"])


def downgrade():
    op.drop_table("code_sandbox_runs")
