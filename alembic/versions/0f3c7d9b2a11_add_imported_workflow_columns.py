"""add_imported_workflow_columns

Revision ID: 0f3c7d9b2a11
Revises: f7c9a2d4b1e6
Create Date: 2026-05-02 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0f3c7d9b2a11"
down_revision: Union[str, Sequence[str], None] = "f7c9a2d4b1e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("imported_source_kind", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("imported_source_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("imported_source_run_uuid", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("imported_config_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("imported_copy_mode", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("imported_source_complete", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_column("imported_source_complete")
        batch_op.drop_column("imported_copy_mode")
        batch_op.drop_column("imported_config_path")
        batch_op.drop_column("imported_source_run_uuid")
        batch_op.drop_column("imported_source_path")
        batch_op.drop_column("imported_source_kind")