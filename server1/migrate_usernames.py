"""
Migration: Add username column to users table and slug column to projects table.

Run once to add the new columns:
    python -m server1.migrate_usernames

This only adds the columns — it does NOT backfill values.
Use `python -m server1.set_usernames auto` to backfill usernames,
and `python -m server1.set_usernames slugify-projects` to backfill project slugs.
"""

import sqlite3
import sys
from pathlib import Path

from server1.config import DB_FILE


def migrate():
    """Add username column to users and slug column to projects."""
    if not DB_FILE.exists():
        print(f"ERROR: Database not found at {DB_FILE}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_FILE))
    cursor = conn.cursor()

    changes = []

    # --- users.username ---
    cursor.execute("PRAGMA table_info(users)")
    user_cols = [row[1] for row in cursor.fetchall()]

    if "username" not in user_cols:
        # SQLite cannot add a UNIQUE column directly; add nullable column
        # first, then create a unique index.
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username "
            "ON users(username) WHERE username IS NOT NULL"
        )
        changes.append("Added 'username' column to users table (with unique index)")
    else:
        # Ensure the index exists even if column was added previously
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username "
            "ON users(username) WHERE username IS NOT NULL"
        )
        print("Column 'username' already exists on users — skipping.")

    # --- projects.slug ---
    cursor.execute("PRAGMA table_info(projects)")
    proj_cols = [row[1] for row in cursor.fetchall()]

    if "slug" not in proj_cols:
        cursor.execute(
            "ALTER TABLE projects ADD COLUMN slug TEXT"
        )
        changes.append("Added 'slug' column to projects table")
    else:
        print("Column 'slug' already exists on projects — skipping.")

    conn.commit()
    conn.close()

    if changes:
        for c in changes:
            print(f"✓ {c}")
        print("\nMigration complete.")
        print("Next steps:")
        print("  python -m server1.set_usernames auto           # backfill usernames")
        print("  python -m server1.set_usernames slugify-projects  # backfill project slugs")
    else:
        print("Nothing to migrate — all columns already exist.")


if __name__ == "__main__":
    migrate()
