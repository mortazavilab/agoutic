"""add_local_max_task_memory_gb_to_dogme_jobs

Revision ID: f7c9a2d4b1e6
Revises: e6b1a4d9c2f3
Create Date: 2026-04-29 12:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7c9a2d4b1e6"
down_revision: Union[str, Sequence[str], None] = "e6b1a4d9c2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("local_max_task_memory_gb", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_column("local_max_task_memory_gb")