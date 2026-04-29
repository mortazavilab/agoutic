"""add_local_max_task_cpus_to_dogme_jobs

Revision ID: e6b1a4d9c2f3
Revises: d4e5f6a7b8c9
Create Date: 2026-04-29 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6b1a4d9c2f3"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("local_max_task_cpus", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_column("local_max_task_cpus")