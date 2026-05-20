"""add workflow_key and nullable mode

Revision ID: 8c5f4d3e2b1a
Revises: 0f3c7d9b2a11
Create Date: 2026-05-19 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8c5f4d3e2b1a"
down_revision: Union[str, Sequence[str], None] = "0f3c7d9b2a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("workflow_key", sa.String(), nullable=True, server_default="dogme"))
        batch_op.create_index("ix_dogme_jobs_workflow_key", ["workflow_key"], unique=False)
        batch_op.alter_column("mode", existing_type=sa.String(), nullable=True)

    op.execute("UPDATE dogme_jobs SET workflow_key = 'dogme' WHERE workflow_key IS NULL OR TRIM(workflow_key) = ''")

    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.alter_column(
            "workflow_key",
            existing_type=sa.String(),
            nullable=False,
            server_default="dogme",
        )


def downgrade() -> None:
    op.execute("UPDATE dogme_jobs SET mode = 'DNA' WHERE mode IS NULL")

    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.alter_column("mode", existing_type=sa.String(), nullable=False)
        batch_op.drop_index("ix_dogme_jobs_workflow_key")
        batch_op.drop_column("workflow_key")