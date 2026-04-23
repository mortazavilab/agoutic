from __future__ import annotations

from pathlib import Path


def next_workflow_number(project_dir: Path) -> int:
    """Compute the next workflow number by scanning existing workflow* dirs."""
    max_n = 0
    if project_dir.exists():
        for child in project_dir.iterdir():
            if child.is_dir() and child.name.startswith("workflow"):
                try:
                    n = int(child.name[len("workflow"):])
                    max_n = max(max_n, n)
                except ValueError:
                    continue
    return max_n + 1


def workflow_dir_name(workflow_index: int) -> str:
    return f"workflow{workflow_index}"