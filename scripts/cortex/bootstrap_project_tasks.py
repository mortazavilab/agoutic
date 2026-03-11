#!/usr/bin/env python3
"""Bootstrap persistent project tasks from existing workflow history.

Usage:
    python scripts/cortex/bootstrap_project_tasks.py
    python scripts/cortex/bootstrap_project_tasks.py --project-id <project_id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cortex.db import SessionLocal, init_db_sync
from cortex.models import Project, ProjectBlock
from cortex.task_service import sync_project_tasks


def _project_ids(session, requested_project_id: str | None) -> list[str]:
    if requested_project_id:
        return [requested_project_id]

    ids = {
        project_id
        for project_id in session.execute(select(Project.id)).scalars().all()
    }
    ids.update(
        project_id
        for project_id in session.execute(select(ProjectBlock.project_id).distinct()).scalars().all()
    )
    return sorted(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap project tasks from workflow history")
    parser.add_argument("--project-id", help="Seed tasks for only one project")
    args = parser.parse_args()

    init_db_sync()
    session = SessionLocal()
    try:
        project_ids = _project_ids(session, args.project_id)
        if not project_ids:
            print("No projects found.")
            return 0

        seeded = 0
        for project_id in project_ids:
            tasks = sync_project_tasks(session, project_id)
            print(f"{project_id}: {len(tasks)} active task(s)")
            seeded += 1

        print(f"Seeded tasks for {seeded} project(s).")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())