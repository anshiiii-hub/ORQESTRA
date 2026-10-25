"""Accounts, workspace roles and server-side sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "memberships",
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member', 'viewer')", name="ck_membership_role"),
    )
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_index("ix_login_sessions_expires_at", "login_sessions", ["expires_at"])
    with op.batch_alter_table("workflows") as batch:
        batch.add_column(sa.Column("workspace_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("created_by", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_workflows_workspace", "workspaces", ["workspace_id"], ["id"])
        batch.create_foreign_key("fk_workflows_creator", "users", ["created_by"], ["id"])
        batch.create_index("ix_workflows_workspace_id", ["workspace_id"])


def downgrade():
    with op.batch_alter_table("workflows") as batch:
        batch.drop_index("ix_workflows_workspace_id")
        batch.drop_constraint("fk_workflows_creator", type_="foreignkey")
        batch.drop_constraint("fk_workflows_workspace", type_="foreignkey")
        batch.drop_column("created_by")
        batch.drop_column("workspace_id")
    op.drop_table("login_sessions")
    op.drop_table("memberships")
    op.drop_table("workspaces")
    op.drop_table("users")
