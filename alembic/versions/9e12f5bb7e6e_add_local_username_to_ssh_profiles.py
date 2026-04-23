"""add_local_username_to_ssh_profiles

Revision ID: 9e12f5bb7e6e
Revises: b95a2c38062c
Create Date: 2026-03-16 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e12f5bb7e6e'
down_revision: Union[str, Sequence[str], None] = 'b95a2c38062c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.add_column(sa.Column("local_username", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.drop_column("local_username")
