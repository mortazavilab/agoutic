#!/usr/bin/env python3
"""
Admin CLI for managing usernames and project slugs.

Runs directly against the SQLite database — no authentication required.
Intended for server administrators working from the command line.

Usage:
    python scripts/cortex/set_usernames.py list                  # list all users
    python scripts/cortex/set_usernames.py set <email> <username>
    python scripts/cortex/set_usernames.py auto
    python scripts/cortex/set_usernames.py slugify-projects
    python scripts/cortex/set_usernames.py migrate-dirs
"""

import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cortex.config import AGOUTIC_DATA, DB_FILE

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,60}$")


def validate_username(username: str) -> bool:
    """Return True if username is valid."""
    return bool(USERNAME_PATTERN.match(username))


def slugify(text: str, max_len: int = 40) -> str:
    """Convert arbitrary text to a filesystem-safe slug.

    Rules:
    - Lowercase
    - Replace spaces and special chars with hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    - Truncate to max_len
    """
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    return s[:max_len] or "project"


def email_to_username(email: str) -> str:
    """Derive a username candidate from an email address.

    ``eli.garcia@gmail.com`` → ``eli-garcia``
    """
    local = email.split("@")[0]
    return slugify(local, max_len=31)


def _dedup(slug: str, existing: set[str]) -> str:
    """Append a numeric suffix if slug already exists."""
    if slug not in existing:
        return slug
    for i in range(2, 10000):
        candidate = f"{slug}-{i}"
        if candidate not in existing:
            return candidate
    raise RuntimeError(f"Could not deduplicate slug: {slug}")


# ---------------------------------------------------------------------------
# Database helpers (raw sqlite3 — no ORM, no async)
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    if not DB_FILE.exists():
        print(f"ERROR: Database not found at {DB_FILE}")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list():
    """List all users with their username status."""
    conn = _connect()
    rows = conn.execute(
        "SELECT id, email, display_name, username, role, is_active FROM users ORDER BY email"
    ).fetchall()
    conn.close()

    if not rows:
        print("No users found.")
        return

    print(f"{'Email':<40} {'Username':<25} {'Display Name':<25} {'Role':<8} {'Active'}")
    print("-" * 130)
    for r in rows:
        uname = r["username"] or "(not set)"
        dname = r["display_name"] or ""
        active = "✓" if r["is_active"] else "✗"
        print(f"{r['email']:<40} {uname:<25} {dname:<25} {r['role']:<8} {active}")
    print(f"\nTotal: {len(rows)} users")


def cmd_set(email: str, username: str):
    """Set username for a specific user by email."""
    if not validate_username(username):
        print(f"ERROR: Invalid username '{username}'.")
        print("Must match: ^[a-z0-9][a-z0-9_-]{{1,30}}$")
        sys.exit(1)

    conn = _connect()

    # Check the user exists
    user = conn.execute("SELECT id, username FROM users WHERE email = ?", (email,)).fetchone()
    if not user:
        print(f"ERROR: No user found with email '{email}'")
        conn.close()
        sys.exit(1)

    # Check uniqueness
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ? AND id != ?", (username, user["id"])
    ).fetchone()
    if existing:
        print(f"ERROR: Username '{username}' is already taken.")
        conn.close()
        sys.exit(1)

    old_username = user["username"]

    # Update DB
    conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, user["id"]))
    conn.commit()

    # Create user home directory
    user_home = AGOUTIC_DATA / "users" / username
    user_home.mkdir(parents=True, exist_ok=True)

    # If there was a previous username, move the directory
    if old_username and old_username != username:
        old_home = AGOUTIC_DATA / "users" / old_username
        if old_home.exists():
            print(f"Moving {old_home} → {user_home}")
            # Move contents if new dir is empty, or merge
            if user_home.exists() and any(user_home.iterdir()):
                print(f"WARNING: Target {user_home} is not empty. Skipping move.")
            else:
                if user_home.exists():
                    user_home.rmdir()
                shutil.move(str(old_home), str(user_home))
            # Update nextflow_work_dir paths in dogme_jobs
            _update_work_dir_paths(conn, str(old_home), str(user_home))
            conn.commit()

    conn.close()
    print(f"✓ Set username for {email} → {username}")
    print(f"  Home directory: {user_home}")


def cmd_auto():
    """Auto-generate usernames for all users that don't have one."""
    conn = _connect()

    users = conn.execute(
        "SELECT id, email, display_name, username FROM users WHERE username IS NULL ORDER BY email"
    ).fetchall()

    if not users:
        print("All users already have usernames.")
        conn.close()
        return

    # Gather existing usernames for dedup
    existing = set()
    for row in conn.execute("SELECT username FROM users WHERE username IS NOT NULL").fetchall():
        existing.add(row["username"])

    changes = []
    for u in users:
        # Try display_name first, fallback to email prefix
        if u["display_name"]:
            candidate = slugify(u["display_name"], max_len=31)
        else:
            candidate = email_to_username(u["email"])

        if not candidate or not validate_username(candidate):
            candidate = email_to_username(u["email"])

        if not candidate or not validate_username(candidate):
            candidate = "user"

        username = _dedup(candidate, existing)
        existing.add(username)

        conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, u["id"]))

        # Create home dir
        user_home = AGOUTIC_DATA / "users" / username
        user_home.mkdir(parents=True, exist_ok=True)

        changes.append((u["email"], username))

    conn.commit()
    conn.close()

    for email, uname in changes:
        print(f"  {email:<40} → {uname}")
    print(f"\n✓ Set usernames for {len(changes)} users.")


def cmd_slugify_projects():
    """Auto-generate slugs for all projects that don't have one."""
    conn = _connect()

    projects = conn.execute(
        "SELECT id, name, owner_id, slug FROM projects WHERE slug IS NULL ORDER BY name"
    ).fetchall()

    if not projects:
        print("All projects already have slugs.")
        conn.close()
        return

    # Gather existing slugs per owner for dedup
    existing_per_owner: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT owner_id, slug FROM projects WHERE slug IS NOT NULL"
    ).fetchall():
        existing_per_owner.setdefault(row["owner_id"], set()).add(row["slug"])

    changes = []
    for p in projects:
        owner_slugs = existing_per_owner.setdefault(p["owner_id"], set())
        candidate = slugify(p["name"])
        slug = _dedup(candidate, owner_slugs)
        owner_slugs.add(slug)

        conn.execute("UPDATE projects SET slug = ? WHERE id = ?", (slug, p["id"]))

        # Get owner username to create directory
        owner = conn.execute(
            "SELECT username FROM users WHERE id = ?", (p["owner_id"],)
        ).fetchone()
        if owner and owner["username"]:
            proj_dir = AGOUTIC_DATA / "users" / owner["username"] / slug
            proj_dir.mkdir(parents=True, exist_ok=True)
            data_dir = proj_dir / "data"
            data_dir.mkdir(exist_ok=True)

        changes.append((p["name"], slug, p["owner_id"]))

    conn.commit()
    conn.close()

    for name, slug, _ in changes:
        print(f"  {name:<40} → {slug}")
    print(f"\n✓ Set slugs for {len(changes)} projects.")


def cmd_migrate_dirs():
    """Migrate existing UUID-based directories to human-readable paths.

    For each project with an owner that has a username and a project slug:
    - Creates AGOUTIC_DATA/users/{username}/{slug}/
    - Moves contents from the old UUID path (if it exists)
    - Updates nextflow_work_dir in dogme_jobs table
    """
    conn = _connect()

    projects = conn.execute("""
        SELECT p.id as project_id, p.slug, p.owner_id, u.username
        FROM projects p
        JOIN users u ON p.owner_id = u.id
        WHERE p.slug IS NOT NULL AND u.username IS NOT NULL
    """).fetchall()

    if not projects:
        print("No projects with both username and slug found. Run 'auto' and 'slugify-projects' first.")
        conn.close()
        return

    migrated = 0
    skipped = 0

    for p in projects:
        old_dir = AGOUTIC_DATA / "users" / p["owner_id"] / p["project_id"]
        new_dir = AGOUTIC_DATA / "users" / p["username"] / p["slug"]

        if not old_dir.exists():
            # No old directory — just make sure new one exists
            new_dir.mkdir(parents=True, exist_ok=True)
            (new_dir / "data").mkdir(exist_ok=True)
            skipped += 1
            continue

        if new_dir.exists() and any(new_dir.iterdir()):
            print(f"  SKIP: {new_dir} already has content (old: {old_dir})")
            skipped += 1
            continue

        # Move old → new
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if new_dir.exists():
            new_dir.rmdir()  # remove empty target so shutil.move works
        print(f"  MOVE: {old_dir} → {new_dir}")
        shutil.move(str(old_dir), str(new_dir))
        (new_dir / "data").mkdir(exist_ok=True)

        # Update dogme_jobs paths
        _update_work_dir_paths(conn, str(old_dir), str(new_dir))
        migrated += 1

    conn.commit()
    conn.close()

    print(f"\n✓ Migrated {migrated} project directories ({skipped} skipped).")


def _update_work_dir_paths(conn: sqlite3.Connection, old_prefix: str, new_prefix: str):
    """Update nextflow_work_dir in dogme_jobs that starts with old_prefix."""
    # Check if dogme_jobs table exists (it's in the launchpad DB, might be shared)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if "dogme_jobs" not in tables:
        return

    rows = conn.execute(
        "SELECT run_uuid, nextflow_work_dir FROM dogme_jobs WHERE nextflow_work_dir LIKE ?",
        (old_prefix + "%",),
    ).fetchall()

    for r in rows:
        new_path = r["nextflow_work_dir"].replace(old_prefix, new_prefix, 1)
        conn.execute(
            "UPDATE dogme_jobs SET nextflow_work_dir = ? WHERE run_uuid = ?",
            (new_path, r["run_uuid"]),
        )
        print(f"    Updated job {r['run_uuid']}: {r['nextflow_work_dir']} → {new_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        cmd_list()
    elif cmd == "set":
        if len(sys.argv) != 4:
            print("Usage: python -m cortex.set_usernames set <email> <username>")
            sys.exit(1)
        cmd_set(sys.argv[2], sys.argv[3])
    elif cmd == "auto":
        cmd_auto()
    elif cmd == "slugify-projects":
        cmd_slugify_projects()
    elif cmd == "migrate-dirs":
        cmd_migrate_dirs()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
