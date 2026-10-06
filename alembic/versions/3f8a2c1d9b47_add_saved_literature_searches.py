"""add_saved_literature_searches

Revision ID: 3f8a2c1d9b47
Revises: 12bc34de56f7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "3f8a2c1d9b47"
down_revision: Union[str, Sequence[str], None] = "12bc34de56f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_literature_searches",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("filters_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("known_pmids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("alert_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_saved_literature_searches_user_id",
        "saved_literature_searches",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_saved_literature_searches_user_id", table_name="saved_literature_searches")
    op.drop_table("saved_literature_searches")
