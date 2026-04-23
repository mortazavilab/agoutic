"""add_remote_profile_defaults_to_ssh_profiles

Revision ID: c7a2e7b6b2df
Revises: 9e12f5bb7e6e
Create Date: 2026-03-16 23:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a2e7b6b2df'
down_revision: Union[str, Sequence[str], None] = '9e12f5bb7e6e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.add_column(sa.Column("default_slurm_account", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_slurm_partition", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_slurm_gpu_account", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_slurm_gpu_partition", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_remote_input_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_remote_work_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("default_remote_output_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ssh_profiles") as batch_op:
        batch_op.drop_column("default_remote_output_path")
        batch_op.drop_column("default_remote_work_path")
        batch_op.drop_column("default_remote_input_path")
        batch_op.drop_column("default_slurm_gpu_partition")
        batch_op.drop_column("default_slurm_gpu_account")
        batch_op.drop_column("default_slurm_partition")
        batch_op.drop_column("default_slurm_account")