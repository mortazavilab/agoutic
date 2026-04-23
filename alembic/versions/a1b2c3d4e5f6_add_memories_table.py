"""add_memories_table

Revision ID: a1b2c3d4e5f6
Revises: f2b4a1d8c6e0
Create Date: 2026-04-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "1a7c0b4d2e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("project_id", sa.String(), nullable=True, index=True),
        sa.Column("category", sa.String(), nullable=False, server_default="custom"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_data", sa.Text(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="user_manual"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("related_block_id", sa.String(), nullable=True),
        sa.Column("related_file_id", sa.String(), nullable=True, index=True),
        sa.Column("tags_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Composite index for common query pattern: user's active memories in a project
    op.create_index(
        "ix_memories_user_project_active",
        "memories",
        ["user_id", "project_id", "is_deleted"],
    )


def downgrade() -> None:
    op.drop_index("ix_memories_user_project_active", table_name="memories")
    op.drop_table("memories")
