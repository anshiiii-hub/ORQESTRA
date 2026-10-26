"""Store durable deliverables, verifiable report artifacts, and verification audits."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_workspace_id", "artifacts", ["workspace_id"])
    op.create_index("ix_artifacts_workflow_id", "artifacts", ["workflow_id"])
    op.create_index("ix_artifacts_task_id", "artifacts", ["task_id"])
    op.create_index("ix_artifacts_artifact_type", "artifacts", ["artifact_type"])

    op.create_table(
        "verification_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("criteria_json", sa.JSON(), nullable=False),
        sa.Column("defects_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_verification_reports_workspace_id", "verification_reports", ["workspace_id"]
    )
    op.create_index("ix_verification_reports_workflow_id", "verification_reports", ["workflow_id"])
    op.create_index("ix_verification_reports_artifact_id", "verification_reports", ["artifact_id"])
    op.create_index("ix_verification_reports_decision", "verification_reports", ["decision"])


def downgrade():
    op.drop_index("ix_verification_reports_decision", table_name="verification_reports")
    op.drop_index("ix_verification_reports_artifact_id", table_name="verification_reports")
    op.drop_index("ix_verification_reports_workflow_id", table_name="verification_reports")
    op.drop_index("ix_verification_reports_workspace_id", table_name="verification_reports")
    op.drop_table("verification_reports")

    op.drop_index("ix_artifacts_artifact_type", table_name="artifacts")
    op.drop_index("ix_artifacts_task_id", table_name="artifacts")
    op.drop_index("ix_artifacts_workflow_id", table_name="artifacts")
    op.drop_index("ix_artifacts_workspace_id", table_name="artifacts")
    op.drop_table("artifacts")
