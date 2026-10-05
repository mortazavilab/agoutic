"""add_user_ncbi_credentials

Revision ID: 12bc34de56f7
Revises: a8c4e1f7b2d9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "12bc34de56f7"
down_revision: Union[str, Sequence[str], None] = "a8c4e1f7b2d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_ncbi_credentials",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_user_ncbi_credentials_user_id", "user_ncbi_credentials", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_ncbi_credentials_user_id", table_name="user_ncbi_credentials")
    op.drop_table("user_ncbi_credentials")
