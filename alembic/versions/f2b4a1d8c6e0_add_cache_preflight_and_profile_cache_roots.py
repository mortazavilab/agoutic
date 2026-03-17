"""add_cache_preflight_and_profile_cache_roots

Revision ID: f2b4a1d8c6e0
Revises: e3a1b5c2d9f4
Create Date: 2026-03-17 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f2b4a1d8c6e0"
down_revision: Union[str, Sequence[str], None] = "e3a1b5c2d9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("cache_preflight_json", sa.JSON(), nullable=True))

    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.add_column(sa.Column("default_remote_reference_cache_root", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_remote_data_cache_root", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.drop_column("default_remote_data_cache_root")
        batch_op.drop_column("default_remote_reference_cache_root")

    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_column("cache_preflight_json")
