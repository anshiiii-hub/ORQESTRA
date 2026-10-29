"""Store atomic login-attempt windows in the primary database."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_attempts",
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempts > 0", name="ck_login_attempts_positive"),
        sa.PrimaryKeyConstraint("email_hash"),
    )
    op.create_index("ix_login_attempts_window_started_at", "login_attempts", ["window_started_at"])


def downgrade():
    op.drop_index("ix_login_attempts_window_started_at", table_name="login_attempts")
    op.drop_table("login_attempts")
