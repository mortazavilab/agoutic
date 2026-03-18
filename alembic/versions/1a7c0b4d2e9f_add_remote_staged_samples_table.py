"""add_remote_staged_samples_table

Revision ID: 1a7c0b4d2e9f
Revises: 6d2c9a4f1b7e
Create Date: 2026-03-17 08:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "1a7c0b4d2e9f"
down_revision: Union[str, Sequence[str], None] = "6d2c9a4f1b7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "remote_staged_samples",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("ssh_profile_id", sa.String(), nullable=False),
        sa.Column("ssh_profile_nickname", sa.String(), nullable=True),
        sa.Column("sample_name", sa.String(), nullable=False),
        sa.Column("sample_slug", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("reference_genome_json", sa.JSON(), nullable=True),
        sa.Column("source_path", sa.String(), nullable=False),
        sa.Column("input_fingerprint", sa.String(), nullable=False),
        sa.Column("remote_base_path", sa.String(), nullable=False),
        sa.Column("remote_data_path", sa.String(), nullable=False),
        sa.Column("remote_reference_paths_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_staged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "ssh_profile_id", "sample_slug", name="uq_remote_staged_sample_user_profile_slug"),
    )
    op.create_index(op.f("ix_remote_staged_samples_user_id"), "remote_staged_samples", ["user_id"], unique=False)
    op.create_index(op.f("ix_remote_staged_samples_ssh_profile_id"), "remote_staged_samples", ["ssh_profile_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_remote_staged_samples_ssh_profile_id"), table_name="remote_staged_samples")
    op.drop_index(op.f("ix_remote_staged_samples_user_id"), table_name="remote_staged_samples")
    op.drop_table("remote_staged_samples")