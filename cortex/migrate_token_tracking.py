#!/usr/bin/env python3
"""
Migration: Token Tracking (Feb 2026)

Adds per-message token usage columns to conversation_messages:
  - conversation_messages.prompt_tokens       INTEGER  nullable
  - conversation_messages.completion_tokens   INTEGER  nullable
  - conversation_messages.total_tokens        INTEGER  nullable
  - conversation_messages.model_name          TEXT     nullable

safe to run multiple times — checks for column existence first.
Pre-existing messages will have NULL in these columns (no backfill).
"""

import sqlite3
import sys
import os
from pathlib import Path

# Mirror the path resolution from cortex/config.py so the script works
# in any environment (local dev, Watson, etc.) without hand-editing.
AGOUTIC_CODE = Path(os.getenv("AGOUTIC_CODE", Path(__file__).resolve().parent.parent))
AGOUTIC_DATA = Path(os.getenv("AGOUTIC_DATA", AGOUTIC_CODE / "data"))
DB_FILE = AGOUTIC_DATA / "database" / "agoutic_v24.sqlite"


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

    # 1. conversation_messages.prompt_tokens
    if not _column_exists(cursor, "conversation_messages", "prompt_tokens"):
        cursor.execute("ALTER TABLE conversation_messages ADD COLUMN prompt_tokens INTEGER")
        print("  ✅ Added conversation_messages.prompt_tokens")
        applied += 1
    else:
        print("  ⏭  conversation_messages.prompt_tokens already exists")

    # 2. conversation_messages.completion_tokens
    if not _column_exists(cursor, "conversation_messages", "completion_tokens"):
        cursor.execute("ALTER TABLE conversation_messages ADD COLUMN completion_tokens INTEGER")
        print("  ✅ Added conversation_messages.completion_tokens")
        applied += 1
    else:
        print("  ⏭  conversation_messages.completion_tokens already exists")

    # 3. conversation_messages.total_tokens
    if not _column_exists(cursor, "conversation_messages", "total_tokens"):
        cursor.execute("ALTER TABLE conversation_messages ADD COLUMN total_tokens INTEGER")
        print("  ✅ Added conversation_messages.total_tokens")
        applied += 1
    else:
        print("  ⏭  conversation_messages.total_tokens already exists")

    # 4. conversation_messages.model_name
    if not _column_exists(cursor, "conversation_messages", "model_name"):
        cursor.execute("ALTER TABLE conversation_messages ADD COLUMN model_name TEXT")
        print("  ✅ Added conversation_messages.model_name")
        applied += 1
    else:
        print("  ⏭  conversation_messages.model_name already exists")

    # 5. users.token_limit
    if not _column_exists(cursor, "users", "token_limit"):
        cursor.execute("ALTER TABLE users ADD COLUMN token_limit INTEGER")
        print("  ✅ Added users.token_limit (NULL = unlimited)")
        applied += 1
    else:
        print("  ⏭  users.token_limit already exists")

    conn.commit()
    conn.close()

    if applied:
        print(f"\n✅ Migration complete — {applied} column(s) added.")
    else:
        print("\n✅ Nothing to do — database already up to date.")


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_FILE
    print(f"🔧 Token tracking migration on {path}")
    migrate(path)
