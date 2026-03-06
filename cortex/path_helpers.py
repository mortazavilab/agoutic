"""
File-path and workflow-path resolution helpers.

Extracted from cortex/app.py — pure functions with no database or app
dependencies.  They resolve user-provided paths to Analyzer-compatible
work_dir / filename pairs.

Functions:
    _pick_file_tool          — choose Analyzer tool by file extension
    _resolve_workflow_path   — resolve subpath to absolute directory
    _resolve_file_path       — resolve user path to (work_dir, filename)
"""


def _pick_file_tool(filename: str) -> str:
    """Return the right analyzer tool for a given filename extension."""
    lower = filename.lower()
    if lower.endswith((".csv", ".tsv")):
        return "parse_csv_file"
    elif lower.endswith(".bed"):
        return "parse_bed_file"
    else:
        return "read_file_content"


def _resolve_workflow_path(
    subpath: str,
    default_work_dir: str,
    workflows: list[dict],
) -> str:
    """
    Resolve a user-provided subpath to an absolute directory for `list_job_files`.

    Handles:
      - Empty subpath → default_work_dir (current workflow)
      - "workflow2" → that workflow's work_dir
      - "workflow2/annot" → that workflow's work_dir + "/annot"
      - "annot" → default_work_dir + "/annot"

    Returns the absolute directory path, or default_work_dir if resolution fails.
    """
    if not subpath:
        return default_work_dir

    # Split the subpath to check if the first component is a known workflow
    parts = subpath.replace("\\", "/").split("/", 1)
    first = parts[0].lower()
    remainder = parts[1] if len(parts) > 1 else ""

    # Check if first component matches a workflow folder name
    for wf in workflows:
        wf_dir = wf.get("work_dir", "")
        wf_folder = wf_dir.rstrip("/").rsplit("/", 1)[-1].lower() if wf_dir else ""
        if first == wf_folder:
            base = wf_dir.rstrip("/")
            return f"{base}/{remainder}" if remainder else base

    # Not a workflow name — treat entire subpath as relative to default_work_dir
    if default_work_dir:
        return f"{default_work_dir.rstrip('/')}/{subpath}"

    return default_work_dir


def _resolve_file_path(
    user_path: str,
    default_work_dir: str,
    workflows: list[dict],
) -> tuple[str, str]:
    """
    Resolve a user-provided file path (may include workflow prefix or subdir)
    into (work_dir, filename_or_relpath) for find_file / parse calls.

    Handles:
      - "File.csv"                           → (default_work_dir, "File.csv")
      - "annot/File.csv"                     → (default_work_dir, "File.csv")
                                                 (find_file searches recursively)
      - "workflow2/annot/File.csv"           → (workflow2_dir, "File.csv")

    Returns (resolved_work_dir, filename).  The filename is always just the
    basename so that find_file's recursive search works regardless of subdir.
    """
    user_path = user_path.replace("\\", "/").strip("/")
    parts = user_path.split("/")

    # If there are path components, check if the first one is a workflow folder
    if len(parts) > 1:
        first = parts[0].lower()
        for wf in workflows:
            wf_dir = wf.get("work_dir", "")
            wf_folder = wf_dir.rstrip("/").rsplit("/", 1)[-1].lower() if wf_dir else ""
            if first == wf_folder:
                # Match by sample name too for multi-workflow disambiguation
                return wf_dir, parts[-1]

        # Also try matching by sample name
        for wf in workflows:
            sn = wf.get("sample_name", "").lower()
            if sn and sn in parts[-1].lower():
                return wf.get("work_dir", default_work_dir), parts[-1]

    return default_work_dir, parts[-1]
