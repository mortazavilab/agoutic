"""add workflow identity fields

Revision ID: 4a7d9c2b1f0e
Revises: f2b4a1d8c6e0
Create Date: 2026-04-07 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "4a7d9c2b1f0e"
down_revision = "f2b4a1d8c6e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("workflow_index", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("workflow_alias", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("workflow_folder_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("workflow_display_name", sa.String(), nullable=True))
        batch_op.create_index("ix_dogme_jobs_workflow_index", ["workflow_index"], unique=False)
        batch_op.create_index("ix_dogme_jobs_workflow_alias", ["workflow_alias"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_index("ix_dogme_jobs_workflow_alias")
        batch_op.drop_index("ix_dogme_jobs_workflow_index")
        batch_op.drop_column("workflow_display_name")
        batch_op.drop_column("workflow_folder_name")
        batch_op.drop_column("workflow_alias")
        batch_op.drop_column("workflow_index")