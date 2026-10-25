"""Persist planning budgets, attempts and validated execution plans."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflows", sa.Column("planning", sa.JSON(), nullable=True))
    op.add_column("workflows", sa.Column("execution_plan", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("workflows") as batch:
        batch.drop_column("execution_plan")
        batch.drop_column("planning")
