"""add_transfer_host_to_ssh_profiles

Revision ID: a8c4e1f7b2d9
Revises: b3e7d9f1a4c6
Create Date: 2026-08-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a8c4e1f7b2d9"
down_revision: Union[str, Sequence[str], None] = "b3e7d9f1a4c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.add_column(sa.Column("transfer_host", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.drop_column("transfer_host")