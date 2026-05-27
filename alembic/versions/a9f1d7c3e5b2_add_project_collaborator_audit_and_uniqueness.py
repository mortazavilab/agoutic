"""add project collaborator audit fields and uniqueness

Revision ID: a9f1d7c3e5b2
Revises: c2f4e6a8b0d1
Create Date: 2026-05-27 14:10:00.000000

Maintenance note:
This migration adds a uniqueness guarantee on ``project_access(project_id, user_id)``.
On file-backed SQLite under WAL, treat the uniqueness-creation step as a short
writer-pause maintenance window: normalize legacy rows, verify no duplicates
remain, create the unique index, then verify the invariant before resuming
writes.
"""

from __future__ import annotations

from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9f1d7c3e5b2"
down_revision: Union[str, Sequence[str], None] = "c2f4e6a8b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ROLE_RANK_SQL = "CASE role WHEN 'owner' THEN 2 WHEN 'editor' THEN 1 ELSE 0 END"
_UNIQUE_INDEX_NAME = "uq_project_access_project_user"


def _current_timestamp(connection) -> object:
    return connection.execute(sa.text("SELECT CURRENT_TIMESTAMP")).scalar_one()


def _backfill_owner_rows(connection) -> None:
    fallback_now = _current_timestamp(connection)
    missing_rows = connection.execute(
        sa.text(
            """
            SELECT
                p.id AS project_id,
                p.owner_id AS owner_id,
                p.name AS project_name,
                p.created_at AS project_created_at
            FROM projects p
            LEFT JOIN project_access pa
              ON pa.project_id = p.id
             AND pa.user_id = p.owner_id
            WHERE pa.id IS NULL
            """
        )
    ).mappings().all()

    if not missing_rows:
        return

    connection.execute(
        sa.text(
            """
            INSERT INTO project_access (
                id,
                user_id,
                project_id,
                project_name,
                role,
                invited_by,
                created_at,
                updated_at,
                last_accessed
            ) VALUES (
                :id,
                :user_id,
                :project_id,
                :project_name,
                'owner',
                NULL,
                :created_at,
                :updated_at,
                :last_accessed
            )
            """
        ),
        [
            {
                "id": str(uuid.uuid4()),
                "user_id": row["owner_id"],
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "created_at": row["project_created_at"] or fallback_now,
                "updated_at": row["project_created_at"] or fallback_now,
                "last_accessed": row["project_created_at"] or fallback_now,
            }
            for row in missing_rows
        ],
    )


def _backfill_membership_audit_fields(connection) -> None:
    connection.execute(
        sa.text(
            """
            UPDATE project_access
               SET project_name = COALESCE(
                       (
                           SELECT projects.name
                             FROM projects
                            WHERE projects.id = project_access.project_id
                       ),
                       project_name
                   )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE project_access
               SET invited_by = NULL
             WHERE invited_by IS NOT NULL
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE project_access
               SET created_at = COALESCE(
                       created_at,
                       (
                           SELECT projects.created_at
                             FROM projects
                            WHERE projects.id = project_access.project_id
                       ),
                       CURRENT_TIMESTAMP
                   )
             WHERE created_at IS NULL
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE project_access
               SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
             WHERE updated_at IS NULL
            """
        )
    )


def _normalize_legacy_owner_rows(connection) -> None:
    connection.execute(
        sa.text(
            """
            UPDATE project_access
               SET role = 'editor',
                   updated_at = CURRENT_TIMESTAMP
             WHERE role = 'owner'
               AND EXISTS (
                   SELECT 1
                     FROM projects
                    WHERE projects.id = project_access.project_id
                      AND projects.owner_id != project_access.user_id
               )
            """
        )
    )


def _deduplicate_memberships(connection) -> None:
    connection.execute(
        sa.text(
            f"""
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY project_id, user_id
                        ORDER BY
                            {_ROLE_RANK_SQL} DESC,
                            created_at DESC,
                            last_accessed DESC,
                            id DESC
                    ) AS row_num
                FROM project_access
            )
            DELETE FROM project_access
             WHERE id IN (
                 SELECT id
                   FROM ranked
                  WHERE row_num > 1
             )
            """
        )
    )


def _verify_preflight(connection) -> None:
    duplicate_rows = connection.execute(
        sa.text(
            """
            SELECT project_id, user_id, COUNT(*) AS row_count
              FROM project_access
             GROUP BY project_id, user_id
            HAVING COUNT(*) > 1
            """
        )
    ).mappings().all()
    if duplicate_rows:
        raise RuntimeError(
            f"project_access uniqueness preflight failed; duplicate memberships remain: {duplicate_rows}"
        )

    missing_owner_rows = connection.execute(
        sa.text(
            """
            SELECT p.id AS project_id, p.owner_id AS owner_id
              FROM projects p
              LEFT JOIN project_access pa
                ON pa.project_id = p.id
               AND pa.user_id = p.owner_id
               AND pa.role = 'owner'
             WHERE pa.id IS NULL
            """
        )
    ).mappings().all()
    if missing_owner_rows:
        raise RuntimeError(
            f"project_access owner-row invariant failed; missing canonical owner rows: {missing_owner_rows}"
        )

    conflicting_owner_rows = connection.execute(
        sa.text(
            """
            SELECT pa.project_id, pa.user_id
              FROM project_access pa
              JOIN projects p
                ON p.id = pa.project_id
             WHERE pa.role = 'owner'
               AND pa.user_id != p.owner_id
            """
        )
    ).mappings().all()
    if conflicting_owner_rows:
        raise RuntimeError(
            f"project_access owner-row invariant failed; non-canonical owner rows remain: {conflicting_owner_rows}"
        )


def upgrade() -> None:
    with op.batch_alter_table("project_access") as batch_op:
        batch_op.add_column(sa.Column("invited_by", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    connection = op.get_bind()
    _backfill_owner_rows(connection)
    _backfill_membership_audit_fields(connection)
    _normalize_legacy_owner_rows(connection)
    _deduplicate_memberships(connection)
    _verify_preflight(connection)

    with op.batch_alter_table("project_access") as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        )

    op.create_index(
        _UNIQUE_INDEX_NAME,
        "project_access",
        ["project_id", "user_id"],
        unique=True,
    )

    _verify_preflight(connection)


def downgrade() -> None:
    op.drop_index(_UNIQUE_INDEX_NAME, table_name="project_access")

    with op.batch_alter_table("project_access") as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("created_at")
        batch_op.drop_column("invited_by")