"""Store workspace approvals and policy-gated tool calls for write gating."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "approvals",
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
        sa.Column("tool_call_id", sa.String(36), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approvals_workspace_id", "approvals", ["workspace_id"])
    op.create_index("ix_approvals_workflow_id", "approvals", ["workflow_id"])
    op.create_index("ix_approvals_task_id", "approvals", ["task_id"])
    op.create_index("ix_approvals_tool_call_id", "approvals", ["tool_call_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_expires_at", "approvals", ["expires_at"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("attempt_id", sa.String(36), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("effect", sa.String(64), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "approval_id",
            sa.String(36),
            sa.ForeignKey("approvals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("idempotency_key", sa.String(128), unique=True, nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("args_redacted_json", sa.JSON(), nullable=False),
        sa.Column("result_ref", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_calls_workspace_id", "tool_calls", ["workspace_id"])
    op.create_index("ix_tool_calls_task_id", "tool_calls", ["task_id"])
    op.create_index("ix_tool_calls_attempt_id", "tool_calls", ["attempt_id"])
    op.create_index("ix_tool_calls_status", "tool_calls", ["status"])
    op.create_index("ix_tool_calls_approval_id", "tool_calls", ["approval_id"])


def downgrade():
    op.drop_table("tool_calls")
    op.drop_table("approvals")
