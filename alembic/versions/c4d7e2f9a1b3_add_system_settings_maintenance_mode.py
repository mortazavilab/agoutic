"""add system settings maintenance mode

Revision ID: c4d7e2f9a1b3
Revises: a9f1d7c3e5b2
Create Date: 2026-05-28 15:30:00.000000

Maintenance note:
This migration creates a small `system_settings` table and inserts three
default maintenance rows. Treat the DDL plus short seed insert as a brief
writer-pause maintenance window on SQLite-backed deployments.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4d7e2f9a1b3"
down_revision: Union[str, Sequence[str], None] = "a9f1d7c3e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), primary_key=True, nullable=False),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
    )

    settings_table = sa.table(
        "system_settings",
        sa.column("key", sa.String(length=100)),
        sa.column("value", sa.Text()),
        sa.column("updated_by", sa.String()),
    )
    op.bulk_insert(
        settings_table,
        [
            {"key": "maintenance_mode", "value": "false", "updated_by": None},
            {"key": "maintenance_message", "value": "", "updated_by": None},
            {"key": "maintenance_starts_at", "value": "", "updated_by": None},
        ],
    )


def downgrade() -> None:
    op.drop_table("system_settings")