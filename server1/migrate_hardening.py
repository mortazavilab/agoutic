#!/usr/bin/env python3
"""
Migration: Hardening Sprint (Feb 2026)

Adds columns introduced by the security hardening sprint:
  - dogme_jobs.user_id        TEXT  (nullable, for job ownership)
  - projects.is_archived      BOOLEAN DEFAULT 0  (soft-delete)
  - project_access.role       TEXT DEFAULT 'owner'  (RBAC)

Safe to run multiple times — checks for column existence first.
"""

import sqlite3
import sys
from pathlib import Path

# Default DB path matches server1/config.py
DB_FILE = Path(__file__).resolve().parent.parent / "data" / "database" / "agoutic_v24.sqlite"


def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def migrate(db_path: Path) -> None:
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        print("   Run init_db.py first, or let the servers create it via create_all().")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    applied = 0

    # 1. dogme_jobs.user_id
    if not _column_exists(cursor, "dogme_jobs", "user_id"):
        cursor.execute("ALTER TABLE dogme_jobs ADD COLUMN user_id TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON dogme_jobs(user_id)")
        print("  ✅ Added dogme_jobs.user_id")
        applied += 1
    else:
        print("  ⏭  dogme_jobs.user_id already exists")

    # 2. projects.is_archived
    if not _column_exists(cursor, "projects", "is_archived"):
        cursor.execute("ALTER TABLE projects ADD COLUMN is_archived BOOLEAN DEFAULT 0")
        print("  ✅ Added projects.is_archived")
        applied += 1
    else:
        print("  ⏭  projects.is_archived already exists")

    # 3. project_access.role
    if not _column_exists(cursor, "project_access", "role"):
        cursor.execute("ALTER TABLE project_access ADD COLUMN role TEXT DEFAULT 'owner'")
        print("  ✅ Added project_access.role")
        applied += 1
    else:
        print("  ⏭  project_access.role already exists")

    conn.commit()
    conn.close()

    if applied:
        print(f"\n✅ Migration complete — {applied} column(s) added.")
    else:
        print("\n✅ Nothing to do — database already up to date.")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_FILE
    print(f"🔧 Hardening migration on {path}")
    migrate(path)
