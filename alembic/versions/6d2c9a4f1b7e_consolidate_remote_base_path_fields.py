"""consolidate_remote_base_path_fields

Revision ID: 6d2c9a4f1b7e
Revises: f2b4a1d8c6e0
Create Date: 2026-03-17 07:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6d2c9a4f1b7e"
down_revision: Union[str, Sequence[str], None] = "f2b4a1d8c6e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.alter_column("default_remote_input_path", new_column_name="remote_base_path")
        batch_op.drop_column("default_remote_work_path")
        batch_op.drop_column("default_remote_output_path")
        batch_op.drop_column("default_remote_reference_cache_root")
        batch_op.drop_column("default_remote_data_cache_root")


def downgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.add_column(sa.Column("default_remote_data_cache_root", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_remote_reference_cache_root", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_remote_output_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_remote_work_path", sa.String(), nullable=True))
        batch_op.alter_column("remote_base_path", new_column_name="default_remote_input_path")