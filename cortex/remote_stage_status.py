def _make_stage_part(status: str, progress_percent: int, message: str, details: list[dict] | None = None) -> dict:
    part = {
        "status": status,
        "progress_percent": progress_percent,
        "message": message,
    }
    if details is not None:
        part["details"] = details
    return part


def _stage_part_progress(parts: dict | None) -> int:
    values: list[int] = []
    for key in ("references", "data"):
        part = (parts or {}).get(key) or {}
        try:
            values.append(max(0, min(int(part.get("progress_percent", 0) or 0), 100)))
        except (TypeError, ValueError):
            values.append(0)
    if not values:
        return 0
    return int(sum(values) / len(values))


def _reference_stage_message(reference_statuses: dict[str, str]) -> tuple[str, list[dict]]:
    if not reference_statuses:
        return "Reference assets are ready.", []

    details = []
    counts: dict[str, int] = {}
    for ref_id, raw_status in reference_statuses.items():
        status = (raw_status or "unknown").strip().lower()
        counts[status] = counts.get(status, 0) + 1
        if status == "reused":
            message = f"{ref_id} already staged on the remote profile."
        elif status == "refreshed":
            message = f"{ref_id} refreshed on the remote profile."
        elif status == "staged":
            message = f"{ref_id} staged to the remote profile."
        elif status == "fallback":
            message = f"{ref_id} staged to the remote profile via fallback."
        else:
            message = f"{ref_id} status: {raw_status}"
        details.append({"reference_id": ref_id, "status": status, "message": message})

    if counts.get("reused") == len(reference_statuses):
        return "Reference assets already staged on the remote profile.", details

    summary = []
    for key in ("reused", "refreshed", "staged", "fallback"):
        count = counts.get(key, 0)
        if count:
            label = "already staged" if key == "reused" else key
            summary.append(f"{count} {label}")
    if summary:
        return f"Reference assets ready ({', '.join(summary)}).", details
    return "Reference assets are ready.", details


def _initial_stage_parts(cache_preflight: dict | None) -> dict:
    cache_preflight = cache_preflight or {}
    reference_actions = cache_preflight.get("reference_actions") or []
    data_action = cache_preflight.get("data_action") or {}

    reference_details = []
    reference_needs_transfer = False
    for item in reference_actions:
        action = (item.get("action") or "stage").strip().lower()
        ref_id = item.get("reference_id") or "reference"
        if action == "reuse":
            reference_details.append({
                "reference_id": ref_id,
                "status": "reused",
                "message": f"{ref_id} already staged on the remote profile.",
            })
        else:
            reference_needs_transfer = True
            action_label = "planned refresh" if action == "refresh" else "planned stage"
            reference_details.append({
                "reference_id": ref_id,
                "status": "pending",
                "message": f"{action_label.capitalize()} for {ref_id} on the remote profile.",
            })

    if reference_actions and not reference_needs_transfer:
        references = _make_stage_part(
            "COMPLETED",
            100,
            "Reference assets already staged on the remote profile.",
            reference_details,
        )
    else:
        references = _make_stage_part(
            "RUNNING",
            40,
            "Checking and preparing reference assets on the remote profile...",
            reference_details,
        )

    data_action_name = (data_action.get("action") or "stage").strip().lower()
    if data_action_name == "reuse":
        data = _make_stage_part(
            "COMPLETED",
            100,
            "Sample data already staged on the remote profile.",
        )
    elif references.get("status") == "COMPLETED":
        data = _make_stage_part(
            "RUNNING",
            35,
            "Staging sample data on the remote profile...",
        )
    else:
        data = _make_stage_part(
            "PENDING",
            0,
            "Waiting for reference staging to finish.",
        )

    return {"references": references, "data": data}


def _final_stage_parts(stage_result: dict, existing_parts: dict | None = None) -> dict:
    reference_statuses = stage_result.get("reference_cache_statuses") or {}
    references_message, reference_details = _reference_stage_message(reference_statuses)
    references = _make_stage_part("COMPLETED", 100, references_message, reference_details)

    data_status = (stage_result.get("data_cache_status") or "staged").strip().lower()
    if data_status == "reused":
        data_message = "Sample data already staged on the remote profile."
    elif data_status == "fallback":
        data_message = "Sample data staged to the remote profile via fallback."
    else:
        data_message = "Sample data staged to the remote profile."
    data = _make_stage_part("COMPLETED", 100, data_message)

    if not reference_statuses and existing_parts:
        references = dict((existing_parts or {}).get("references") or references)
        references["status"] = "COMPLETED"
        references["progress_percent"] = 100

    return {"references": references, "data": data}


def _failed_stage_parts(parts: dict | None, error_message: str) -> dict:
    parts = {
        "references": dict((parts or {}).get("references") or _make_stage_part("RUNNING", 40, "Staging reference assets on the remote profile...")),
        "data": dict((parts or {}).get("data") or _make_stage_part("PENDING", 0, "Waiting for reference staging to finish.")),
    }
    lowered = (error_message or "").lower()

    if "reference cache stage failed" in lowered or "reference cache" in lowered:
        parts["references"].update({
            "status": "FAILED",
            "message": f"Reference staging failed: {error_message}",
        })
        if parts["data"].get("status") != "COMPLETED":
            parts["data"].update({
                "status": "PENDING",
                "progress_percent": 0,
                "message": "Sample data did not start because reference staging failed.",
            })
        return parts

    if "input transfer failed" in lowered or "local source path does not exist" in lowered:
        if parts["references"].get("status") != "COMPLETED":
            parts["references"].update({
                "status": "COMPLETED",
                "progress_percent": 100,
                "message": "Reference assets are ready on the remote profile.",
            })
        parts["data"].update({
            "status": "FAILED",
            "message": f"Sample data staging failed: {error_message}",
        })
        return parts

    for part in parts.values():
        if part.get("status") != "COMPLETED":
            part.update({
                "status": "FAILED",
                "message": error_message,
            })
    return parts
