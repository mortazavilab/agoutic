"""persist_dogme_input_mode

Revision ID: b3e7d9f1a4c6
Revises: c4d7e2f9a1b3
Create Date: 2026-08-04 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3e7d9f1a4c6"
down_revision: Union[str, Sequence[str], None] = "c4d7e2f9a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("input_type", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("entry_point", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_column("entry_point")
        batch_op.drop_column("input_type")