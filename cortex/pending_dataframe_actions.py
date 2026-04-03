from __future__ import annotations

import datetime
import json
from typing import Any

from sqlalchemy import select

from common.logging_config import get_logger
from cortex.chat_dataframes import assign_df_ids, extract_embedded_dataframes
from cortex.conversation_state import _build_conversation_state
from cortex.dataframe_actions import execute_local_dataframe_call
from cortex.db import row_to_dict
from cortex.db_helpers import _create_block_internal
from cortex.llm_validators import get_block_payload
from cortex.models import ProjectBlock

logger = get_logger(__name__)


def execute_pending_dataframe_action(session, pending_block: ProjectBlock) -> dict[str, Any]:
    payload = get_block_payload(pending_block)
    action_call = payload.get("action_call") or {}
    tool_name = str(action_call.get("tool") or "").strip()
    params = action_call.get("params") or {}
    summary = str(payload.get("summary") or "Apply saved dataframe action").strip()

    if not tool_name:
        raise ValueError("Pending dataframe action is missing a tool name")

    history_blocks = session.execute(
        select(ProjectBlock)
        .where(ProjectBlock.project_id == pending_block.project_id)
        .order_by(ProjectBlock.seq.asc())
    ).scalars().all()

    pending_payload = dict(payload)
    pending_payload["confirmed_at"] = _utc_now_iso()
    pending_block.status = "CONFIRMED"
    pending_block.payload_json = json.dumps(pending_payload)
    session.commit()
    session.refresh(pending_block)

    try:
        result_payload = execute_local_dataframe_call(tool_name, params, history_blocks=history_blocks)
        result_record = {"tool": tool_name, "params": params, "data": result_payload}
        embedded_dataframes = extract_embedded_dataframes({"cortex": [result_record]}, summary)
        assign_df_ids(embedded_dataframes, history_blocks, {})

        conv_state = _build_conversation_state(
            active_skill=str(payload.get("skill") or "welcome"),
            conversation_history=None,
            history_blocks=history_blocks,
            project_id=pending_block.project_id,
            db=session,
            user_id=pending_block.owner_id,
        )
        _update_conversation_state_with_embedded_dfs(conv_state, embedded_dataframes)

        newest_df = _find_newest_dataframe(embedded_dataframes)
        if newest_df:
            df_id, label, row_count = newest_df
            markdown = f"Applied saved dataframe action: {summary}.\n\nCreated DF{df_id} ({label}, {row_count} rows)."
            pending_payload["result_df_id"] = df_id
        else:
            markdown = f"Applied saved dataframe action: {summary}."

        pending_payload["completed_at"] = _utc_now_iso()
        pending_payload.pop("error", None)
        pending_block.status = "COMPLETED"
        pending_block.payload_json = json.dumps(pending_payload)

        agent_payload = {
            "markdown": markdown,
            "skill": payload.get("skill", "welcome"),
            "model": payload.get("model", "system"),
            "state": conv_state.to_dict(),
            "_dataframes": embedded_dataframes,
            "_provenance": [{
                "source_key": "cortex",
                "tool": tool_name,
                "params": params,
            }],
        }
        agent_block = _create_block_internal(
            session,
            pending_block.project_id,
            "AGENT_PLAN",
            agent_payload,
            status="DONE",
            owner_id=pending_block.owner_id,
        )
        session.commit()
        session.refresh(pending_block)
        return {
            "pending_block": row_to_dict(pending_block),
            "agent_block": row_to_dict(agent_block),
        }
    except Exception as exc:
        pending_payload = dict(payload)
        pending_payload["error"] = str(exc)
        pending_payload["failed_at"] = _utc_now_iso()
        pending_block.status = "FAILED"
        pending_block.payload_json = json.dumps(pending_payload)
        session.commit()
        logger.error(
            "Pending dataframe action failed",
            block_id=pending_block.id,
            tool=tool_name,
            error=str(exc),
            exc_info=True,
        )
        raise


def reject_pending_dataframe_action(session, pending_block: ProjectBlock, reason: str | None = None) -> None:
    payload = dict(get_block_payload(pending_block))
    payload["rejected_at"] = _utc_now_iso()
    if reason:
        payload["rejection_reason"] = reason
    pending_block.status = "REJECTED"
    pending_block.payload_json = json.dumps(payload)
    session.commit()


def _update_conversation_state_with_embedded_dfs(conv_state, embedded_dataframes: dict[str, dict]) -> None:
    if not embedded_dataframes:
        return
    max_new_id = 0
    newest_label = ""
    newest_rows = "?"
    for value in embedded_dataframes.values():
        metadata = value.get("metadata", {})
        df_id = metadata.get("df_id")
        if isinstance(df_id, int) and df_id > max_new_id:
            max_new_id = df_id
            newest_label = metadata.get("label", "")
            newest_rows = metadata.get("row_count", "?")
    if max_new_id <= 0:
        return
    latest = f"DF{max_new_id}"
    conv_state.latest_dataframe = latest
    if not any(item.startswith(latest + " ") or item == latest for item in conv_state.known_dataframes):
        conv_state.known_dataframes.append(f"{latest} ({newest_label}, {newest_rows} rows)")


def _find_newest_dataframe(embedded_dataframes: dict[str, dict]) -> tuple[int, str, Any] | None:
    best: tuple[int, str, Any] | None = None
    for label, payload in embedded_dataframes.items():
        metadata = payload.get("metadata", {})
        df_id = metadata.get("df_id")
        if not isinstance(df_id, int):
            continue
        if best is None or df_id > best[0]:
            best = (df_id, metadata.get("label") or label, metadata.get("row_count", len(payload.get("data", []))))
    return best


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
