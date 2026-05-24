"""add_workflow_usage_summary_to_dogme_jobs

Revision ID: c2f4e6a8b0d1
Revises: 8c5f4d3e2b1a
Create Date: 2026-05-24 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2f4e6a8b0d1"
down_revision: Union[str, Sequence[str], None] = "8c5f4d3e2b1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("workflow_usage_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("workflow_usage_synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_column("workflow_usage_synced_at")
        batch_op.drop_column("workflow_usage_json")