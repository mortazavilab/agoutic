import datetime
import time

import streamlit as st


def create_project_server_side(name: str | None, api_url: str, request_fn) -> dict:
    """Create a project via POST /projects and return {id, slug, name}."""
    project_name = name or f"project-{datetime.datetime.now().strftime('%Y-%m-%d')}"
    try:
        resp = request_fn(
            "POST",
            f"{api_url}/projects",
            json={"name": project_name},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "id": data["id"],
                "slug": data.get("slug", ""),
                "name": data.get("name", project_name),
            }
    except Exception:
        pass

    import uuid as _uuid

    return {"id": str(_uuid.uuid4()), "slug": "", "name": project_name}


def launchpad_headers(internal_api_secret: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if internal_api_secret:
        headers["X-Internal-Secret"] = internal_api_secret
    return headers


def load_user_ssh_profiles(
    user_id: str,
    *,
    launchpad_url: str,
    request_fn,
    internal_api_secret: str | None,
) -> list[dict]:
    if not user_id:
        return []
    try:
        resp = request_fn(
            "GET",
            f"{launchpad_url}/ssh-profiles",
            params={"user_id": user_id},
            headers=launchpad_headers(internal_api_secret),
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            profiles = data.get("profiles")
            return profiles if isinstance(profiles, list) else []
    except Exception:
        return []
    return []


def get_job_debug_info(
    run_uuid: str,
    *,
    api_url: str,
    request_fn,
):
    """Fetch detailed debug information for a failed job via Cortex proxy."""
    try:
        resp = request_fn(
            "GET",
            f"{api_url}/jobs/{run_uuid}/debug",
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json(), None
    except Exception as exc:
        return None, str(exc)
    return None, None


def get_cached_job_status(
    run_uuid: str,
    *,
    api_url: str,
    request_fn,
    timeout_seconds: float,
    cache_seconds: float = 2.0,
):
    """Fetch job status with a short-lived session-state cache for rerenders."""
    if not run_uuid:
        return {}, False

    now = time.time()
    cache_key = f"_job_status_cache_{run_uuid}"
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict):
        fetched_at = float(cached.get("fetched_at") or 0.0)
        cached_status = cached.get("job_status")
        if isinstance(cached_status, dict) and (now - fetched_at) < cache_seconds:
            return cached_status, True

    timeout = float(timeout_seconds or 0) if timeout_seconds else 0.0
    if timeout <= 0:
        timeout = 1.5
    timeout = min(timeout, 1.5)

    try:
        resp = request_fn(
            "GET",
            f"{api_url}/jobs/{run_uuid}/status",
            timeout=timeout,
        )
        if resp.status_code == 200:
            job_status = resp.json()
            if isinstance(job_status, dict):
                st.session_state[cache_key] = {
                    "fetched_at": now,
                    "job_status": job_status,
                }
                transfer_state = str(job_status.get("transfer_state") or "").strip().lower()
                if transfer_state:
                    st.session_state[f"_transfer_state_{run_uuid}"] = transfer_state
                st.session_state[f"_job_polled_at_{run_uuid}"] = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                return job_status, True
    except Exception:
        pass

    if isinstance(cached, dict) and isinstance(cached.get("job_status"), dict):
        return cached["job_status"], False
    return {}, False


def _workflow_highlight_steps(workflow_block: dict) -> list[dict]:
    """Return completed workflow steps worth surfacing in the main chat flow."""
    if not isinstance(workflow_block, dict):
        return []
    payload = workflow_block.get("payload", {})
    steps = payload.get("steps", []) if isinstance(payload, dict) else []
    if not isinstance(steps, list):
        return []

    highlight_kinds = {"GENERATE_PLOT", "INTERPRET_RESULTS", "WRITE_SUMMARY", "RECOMMEND_NEXT"}
    highlights: list[dict] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("kind") not in highlight_kinds:
            continue
        if step.get("status") != "COMPLETED":
            continue
        result = step.get("result")
        if result in (None, "", [], {}):
            continue
        highlights.append(step)
    return highlights


def _block_requires_full_refresh(block: dict) -> bool:
    """Return True when a block needs whole-page reruns, not just fragment refresh."""
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    bstatus = block.get("status")
    if btype == "DOWNLOAD_TASK":
        return bstatus == "RUNNING"
    if btype == "EXECUTION_JOB":
        payload = block.get("payload", {}) if isinstance(block.get("payload"), dict) else {}
        job_status = payload.get("job_status", {}) if isinstance(payload.get("job_status"), dict) else {}
        nested_status = str(job_status.get("status") or "").upper()
        transfer_state = str(job_status.get("transfer_state") or "").strip().lower()
        return (
            bstatus == "RUNNING"
            or nested_status in {"RUNNING", "PENDING"}
            or transfer_state in {"downloading_outputs"}
        )
    if btype == "AGENT_PLAN":
        payload = block.get("payload", {}) if isinstance(block.get("payload"), dict) else {}
        run_uuid = payload.get("_sync_run_uuid", "")
        if run_uuid:
            cached = (st.session_state.get(f"_transfer_state_{run_uuid}") or "").strip().lower()
            if cached in {"downloading_outputs"}:
                return True
    return False


def _find_related_workflow_plan(agent_block: dict, all_blocks: list):
    """Return the nearest prior workflow block that matches an agent plan summary."""
    if not isinstance(agent_block, dict):
        return None

    payload = agent_block.get("payload", {}) if isinstance(agent_block.get("payload"), dict) else {}
    block_id = agent_block.get("id")
    project_id = agent_block.get("project_id")
    markdown = str(payload.get("markdown") or "")
    workflow_plan_block_id = str(payload.get("workflow_plan_block_id") or "").strip()

    import re as _re

    title_match = _re.search(r"^\s{0,3}(?:#{1,6}\s*)?Plan:\s*(.+?)\s*$", markdown, _re.MULTILINE)
    target_title = " ".join(title_match.group(1).split()) if title_match else ""

    block_index = None
    for idx, candidate in enumerate(all_blocks):
        if isinstance(candidate, dict) and candidate.get("id") == block_id:
            block_index = idx
            break
    if block_index is None:
        return None

    prior_workflows = []
    for candidate in reversed(all_blocks[:block_index]):
        if not isinstance(candidate, dict):
            continue
        if candidate.get("type") != "WORKFLOW_PLAN":
            continue
        if project_id and candidate.get("project_id") != project_id:
            continue
        prior_workflows.append(candidate)
        if workflow_plan_block_id and candidate.get("id") == workflow_plan_block_id:
            return candidate

    if not target_title:
        return None

    for candidate in prior_workflows:
        candidate_payload = candidate.get("payload", {}) if isinstance(candidate.get("payload"), dict) else {}
        candidate_title = " ".join(str(candidate_payload.get("title") or candidate_payload.get("summary") or "").split())
        if target_title and candidate_title == target_title:
            return candidate
    return None


def _workflow_status_presentation(raw_status: str) -> tuple[str, str, str]:
    normalized = (raw_status or "pending").strip().lower()
    if normalized in {"completed", "complete", "done", "approved"}:
        return "complete", raw_status.replace("_", " ").title(), "✅"
    if normalized == "deleted":
        return "pending", raw_status.replace("_", " ").title(), "🗑️"
    if normalized in {"failed", "rejected", "cancelled"}:
        return "failed", raw_status.replace("_", " ").title(), "❌"
    if normalized in {"running", "active"}:
        return "running", raw_status.replace("_", " ").title(), "🔄"
    if normalized in {"follow_up", "waiting_approval", "blocked"}:
        return "warning", raw_status.replace("_", " ").title(), "⏸️"
    return "pending", raw_status.replace("_", " ").title(), "📝"
