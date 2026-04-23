"""add_remote_staging_cache_tables

Revision ID: e3a1b5c2d9f4
Revises: c7a2e7b6b2df
Create Date: 2026-03-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3a1b5c2d9f4"
down_revision: Union[str, Sequence[str], None] = "c7a2e7b6b2df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.add_column(sa.Column("reference_cache_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("data_cache_status", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("reference_cache_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("data_cache_path", sa.String(), nullable=True))

    op.create_table(
        "remote_reference_cache",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ssh_profile_id", sa.String(), nullable=False),
        sa.Column("reference_id", sa.String(), nullable=False),
        sa.Column("source_signature", sa.String(), nullable=True),
        sa.Column("source_uri", sa.String(), nullable=True),
        sa.Column("remote_path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ssh_profile_id", "reference_id", name="uq_remote_ref_cache_user_profile_ref"),
    )
    op.create_index("ix_remote_reference_cache_user_id", "remote_reference_cache", ["user_id"])
    op.create_index("ix_remote_reference_cache_ssh_profile_id", "remote_reference_cache", ["ssh_profile_id"])

    op.create_table(
        "remote_input_cache",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ssh_profile_id", sa.String(), nullable=False),
        sa.Column("reference_id", sa.String(), nullable=False),
        sa.Column("input_fingerprint", sa.String(), nullable=False),
        sa.Column("remote_path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "ssh_profile_id",
            "reference_id",
            "input_fingerprint",
            name="uq_remote_input_cache_user_profile_ref_fp",
        ),
    )
    op.create_index("ix_remote_input_cache_user_id", "remote_input_cache", ["user_id"])
    op.create_index("ix_remote_input_cache_ssh_profile_id", "remote_input_cache", ["ssh_profile_id"])


def downgrade() -> None:
    op.drop_table("remote_input_cache")
    op.drop_table("remote_reference_cache")

    with op.batch_alter_table("dogme_jobs") as batch_op:
        batch_op.drop_column("data_cache_path")
        batch_op.drop_column("reference_cache_path")
        batch_op.drop_column("data_cache_status")
        batch_op.drop_column("reference_cache_status")
