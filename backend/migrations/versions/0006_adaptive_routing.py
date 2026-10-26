"""Persist workflow routing baselines and agent selection explanations."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflows",
        sa.Column("routing_mode", sa.String(16), nullable=False, server_default="fixed"),
    )
    op.add_column("tasks", sa.Column("routing_decision", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("routing_decision")
    with op.batch_alter_table("workflows") as batch:
        batch.drop_column("routing_mode")
