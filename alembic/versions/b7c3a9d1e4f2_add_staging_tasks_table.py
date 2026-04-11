"""add_staging_tasks_table

Revision ID: b7c3a9d1e4f2
Revises: a1b2c3d4e5f6
Create Date: 2026-04-11 11:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b7c3a9d1e4f2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "staging_tasks",
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("progress_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(op.f("ix_staging_tasks_status"), "staging_tasks", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_staging_tasks_status"), table_name="staging_tasks")
    op.drop_table("staging_tasks")