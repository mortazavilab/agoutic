"""Stage 1100 — DF/image extraction, block creation, response assembly."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from common import MCPHttpClient
from common.logging_config import get_logger
from cortex.chat_context import ChatContext
from cortex.chat_stages import register_stage
from cortex.chat_dataframes import (
    _find_plot_dataframe_payload,
    apply_overlap_message_hints,
    assign_df_ids,
    collapse_competing_specs,
    deduplicate_plot_specs,
    extract_embedded_dataframes,
    extract_embedded_images,
    find_dataframe_parse_source,
    materialize_direct_set_overlap_plot_dataframes,
    materialize_overlap_plot_dataframes,
    post_df_assignment_plot_fallback,
    rebind_plots_to_new_df,
)
from cortex.chat_sync_handler import _emit_progress, _extract_plot_style_params
from cortex.config import get_service_url
from cortex.dataframe_actions import summarize_dataframe_action
from cortex.db import row_to_dict
from cortex.db_helpers import _create_block_internal, save_conversation_message
from cortex.llm_validators import get_block_payload
from cortex.memory_service import auto_capture_plot
import cortex.job_parameters as job_parameters

logger = get_logger(__name__)

PRIORITY = 1100


async def _build_plot_source_dataframes(
    plot_specs: list[dict],
    embedded_dataframes: dict[str, dict],
    history_blocks: list,
    project_dir_path: Path | None = None,
) -> tuple[dict[str, dict], list[str]]:
    overrides, errors = await _rehydrate_truncated_plot_sources(
        plot_specs,
        embedded_dataframes,
        history_blocks,
        project_dir_path=project_dir_path,
    )
    if not overrides:
        return embedded_dataframes, errors

    merged: dict[str, dict] = {}
    merged.update(overrides)
    merged.update(embedded_dataframes)
    return merged, errors


async def _rehydrate_truncated_plot_sources(
    plot_specs: list[dict],
    embedded_dataframes: dict[str, dict],
    history_blocks: list,
    project_dir_path: Path | None = None,
) -> tuple[dict[str, dict], list[str]]:
    relevant_df_ids = sorted({
        spec.get("df_id")
        for spec in plot_specs
        if str(spec.get("type") or "").strip().lower() in {"venn", "upset"}
        and isinstance(spec.get("df_id"), int)
    })
    if not relevant_df_ids:
        return {}, []

    rehydrated: dict[str, dict] = {}
    errors: list[str] = []
    for df_id in relevant_df_ids:
        payload = _find_plot_dataframe_payload(
            df_id,
            history_blocks,
            current_dataframes=embedded_dataframes,
        )
        if not _is_truncated_plot_payload(payload):
            continue

        parse_source = find_dataframe_parse_source(df_id, history_blocks)
        full_result = await _load_full_parsed_dataframe(
            parse_source,
            payload=payload,
            project_dir_path=project_dir_path,
        )
        if not full_result:
            errors.append(
                f"Could not build accurate {str(next((spec.get('type') for spec in plot_specs if spec.get('df_id') == df_id), 'plot')).strip().lower() or 'plot'} overlap input: "
                f"DF{df_id} only has truncated preview rows and the full source file could not be reloaded."
            )
            continue

        payload_metadata = dict((payload or {}).get("metadata") or {})
        result_metadata = dict(full_result.get("metadata") or {})
        merged_metadata = dict(payload_metadata)
        merged_metadata.update(result_metadata)
        merged_metadata["df_id"] = df_id
        if payload_metadata.get("visible") is not None:
            merged_metadata["visible"] = payload_metadata.get("visible")
        if payload_metadata.get("label"):
            merged_metadata["label"] = payload_metadata.get("label")
        if full_result.get("file_path"):
            merged_metadata["file_path"] = full_result.get("file_path")
        if full_result.get("work_dir"):
            merged_metadata["work_dir"] = full_result.get("work_dir")

        label = str(
            payload_metadata.get("label")
            or full_result.get("file_path")
            or f"DF{df_id}"
        ).strip()
        label = Path(label).name if label else f"DF{df_id}"
        rehydrated[f"{label}_full_df{df_id}"] = {
            "columns": full_result.get("columns", []),
            "data": full_result.get("data", []),
            "row_count": full_result.get("row_count", len(full_result.get("data", []))),
            "metadata": merged_metadata,
        }

    return rehydrated, errors


def _is_truncated_plot_payload(payload: dict | None) -> bool:
    if not payload:
        return False
    metadata = payload.get("metadata") or {}
    if metadata.get("is_truncated"):
        return True
    stored_rows = len(payload.get("data") or [])
    declared_rows = payload.get("row_count") or metadata.get("row_count") or metadata.get("total_rows")
    return isinstance(declared_rows, int) and declared_rows > stored_rows


async def _load_full_parsed_dataframe(
    parse_source: dict | None,
    *,
    payload: dict | None = None,
    project_dir_path: Path | None = None,
) -> dict | None:
    file_path = str((parse_source or {}).get("file_path") or "").strip()

    params: dict = {}
    work_dir = str((parse_source or {}).get("work_dir") or "").strip()
    run_uuid = str((parse_source or {}).get("run_uuid") or "").strip()
    if file_path:
        params = {
            "file_path": file_path,
            "max_rows": None,
        }
        if work_dir:
            params["work_dir"] = work_dir
        elif run_uuid:
            params["run_uuid"] = run_uuid

    if params:
        try:
            analyzer_url = get_service_url("analyzer")
            client = MCPHttpClient(name="analyzer", base_url=analyzer_url)
            await client.connect()
            try:
                result = await client.call_tool("parse_csv_file", **params)
            finally:
                await client.disconnect()
        except Exception as exc:
            logger.warning(
                "Failed to rehydrate truncated plot dataframe",
                file_path=file_path,
                work_dir=work_dir,
                run_uuid=run_uuid,
                error=str(exc),
            )
            result = _load_full_parsed_dataframe_locally(
                parse_source,
                payload=payload,
                project_dir_path=project_dir_path,
            )
            if result:
                return result
            return None

        if isinstance(result, dict) and result.get("data"):
            return result

    return _load_full_parsed_dataframe_locally(
        parse_source,
        payload=payload,
        project_dir_path=project_dir_path,
    )


def _load_full_parsed_dataframe_locally(
    parse_source: dict | None,
    *,
    payload: dict | None = None,
    project_dir_path: Path | None = None,
) -> dict | None:
    full_path = _resolve_local_parse_source_path(
        parse_source,
        payload=payload,
        project_dir_path=project_dir_path,
    )
    if full_path is None:
        return None

    file_path = _select_local_source_label(parse_source, payload, full_path)
    work_dir = str(full_path.parent)
    sep = "\t" if full_path.suffix.lower() == ".tsv" else ","
    try:
        df = pd.read_csv(full_path, sep=sep, low_memory=False)
    except Exception as exc:
        logger.warning(
            "Local dataframe rehydration failed",
            file_path=file_path,
            work_dir=work_dir,
            absolute_path=str(full_path),
            error=str(exc),
        )
        return None

    rows = json.loads(df.to_json(orient="records", default_handler=str))
    return {
        "file_path": file_path,
        "work_dir": work_dir,
        "columns": list(df.columns),
        "row_count": len(df),
        "preview_rows": len(rows),
        "data": rows,
        "metadata": {
            "separator": sep,
            "column_count": len(df.columns),
            "total_rows": len(df),
            "is_truncated": False,
            "local_rehydration": True,
            "resolved_path": str(full_path),
        },
    }


def _resolve_local_parse_source_path(
    parse_source: dict | None,
    *,
    payload: dict | None = None,
    project_dir_path: Path | None = None,
) -> Path | None:
    candidate_files = _candidate_local_source_files(parse_source, payload)
    if not candidate_files:
        return None

    work_dir = str((parse_source or {}).get("work_dir") or "").strip()
    work_root = Path(work_dir).expanduser() if work_dir else None
    project_root = project_dir_path.resolve() if isinstance(project_dir_path, Path) else None
    search_roots = _candidate_local_search_roots(project_root, work_root)

    for candidate in candidate_files:
        resolved = _resolve_candidate_source_path(candidate, search_roots)
        if resolved is not None:
            return resolved

    preferred_parent_name = work_root.name if work_root is not None and work_root.name else ""
    return _search_local_source_by_name(
        candidate_files,
        search_roots,
        preferred_parent_name=preferred_parent_name,
    )


def _candidate_local_source_files(parse_source: dict | None, payload: dict | None) -> list[Path]:
    seen: set[str] = set()
    candidates: list[Path] = []

    def _add(raw_value) -> None:
        for candidate in _extract_file_like_candidates(raw_value):
            key = str(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)

    if parse_source:
        _add(parse_source.get("file_path"))

    metadata = dict((payload or {}).get("metadata") or {})
    _add(metadata.get("file_path"))
    _add(metadata.get("label"))
    return candidates


def _extract_file_like_candidates(raw_value) -> list[Path]:
    value = str(raw_value or "").strip()
    if not value:
        return []

    matches: list[str] = []
    suffixes = {".csv", ".tsv", ".txt"}
    direct_path = Path(value)
    if direct_path.suffix.lower() in suffixes:
        matches.append(value)

    for match in re.findall(r"[A-Za-z0-9_./\\ -]+\.(?:csv|tsv|txt)", value, re.IGNORECASE):
        cleaned = match.strip().strip('"\'')
        if cleaned:
            matches.append(cleaned)

    deduped: list[Path] = []
    seen: set[str] = set()
    for match in matches:
        key = match.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(Path(key))
    return deduped


def _candidate_local_search_roots(
    project_root: Path | None,
    work_root: Path | None,
) -> list[Path]:
    roots: list[Path] = []

    def _add(path: Path | None) -> None:
        if not isinstance(path, Path):
            return
        try:
            resolved = path.resolve()
        except Exception:
            return
        if resolved in roots:
            return
        roots.append(resolved)

    if work_root and work_root.is_absolute():
        _add(work_root)
    if project_root:
        _add(project_root)
        _add(project_root / "data")
        _add(project_root.parent / "data")
        if work_root and work_root.name:
            _add(project_root / work_root.name)
    return roots


def _resolve_candidate_source_path(candidate: Path, search_roots: list[Path]) -> Path | None:
    if candidate.is_absolute():
        return candidate.resolve() if candidate.exists() and candidate.is_file() else None

    for root in search_roots:
        try:
            full_path = (root / candidate).resolve()
        except Exception:
            continue
        if full_path.exists() and full_path.is_file():
            return full_path
    return None


def _search_local_source_by_name(
    candidate_files: list[Path],
    search_roots: list[Path],
    *,
    preferred_parent_name: str = "",
) -> Path | None:
    candidate_names = [candidate.name for candidate in candidate_files if candidate.name]
    if not candidate_names:
        return None

    matches: list[Path] = []
    for root in search_roots:
        if not root.exists() or not root.is_dir():
            continue
        for name in candidate_names:
            try:
                for match in root.rglob(name):
                    if match.is_file():
                        resolved = match.resolve()
                        if resolved not in matches:
                            matches.append(resolved)
            except Exception:
                continue

    if not matches:
        return None

    preferred_parent_name = preferred_parent_name.strip()
    if preferred_parent_name:
        preferred = [path for path in matches if preferred_parent_name in path.parts]
        if preferred:
            matches = preferred

    matches.sort(key=lambda path: (len(path.parts), str(path)))
    return matches[0]


def _select_local_source_label(
    parse_source: dict | None,
    payload: dict | None,
    full_path: Path,
) -> str:
    for raw_value in (
        (parse_source or {}).get("file_path"),
        ((payload or {}).get("metadata") or {}).get("file_path"),
        ((payload or {}).get("metadata") or {}).get("label"),
    ):
        candidates = _extract_file_like_candidates(raw_value)
        if candidates:
            return str(candidates[0])
    return full_path.name


def _drop_inaccurate_truncated_overlap_specs(
    plot_specs: list[dict],
    plot_source_dataframes: dict[str, dict],
    history_blocks: list,
) -> list[str]:
    errors: list[str] = []
    retained_specs: list[dict] = []
    for spec in plot_specs:
        chart_type = str(spec.get("type") or "").strip().lower()
        df_id = spec.get("df_id")
        if chart_type not in {"venn", "upset"} or not isinstance(df_id, int):
            retained_specs.append(spec)
            continue

        payload = _find_plot_dataframe_payload(
            df_id,
            history_blocks,
            current_dataframes=plot_source_dataframes,
        )
        if not _is_truncated_plot_payload(payload):
            retained_specs.append(spec)
            continue

        errors.append(
            f"Could not build accurate {chart_type} overlap input: DF{df_id} only has truncated preview rows and the full source file could not be reloaded."
        )
    plot_specs[:] = retained_specs
    return errors


class ResponseAssemblyStage:
    name = "response_assembly"
    priority = PRIORITY

    async def should_run(self, ctx: ChatContext) -> bool:
        return True

    async def run(self, ctx: ChatContext) -> None:
        # ── Extract DFs & images ──────────────────────────────────────
        ctx.embedded_dataframes = extract_embedded_dataframes(ctx.all_results, ctx.message)
        ctx.embedded_images = extract_embedded_images(ctx.all_results)

        _emit_progress(ctx.request_id, "done", "Complete")

        # ── Assign DF IDs and rebind plots ────────────────────────────
        assign_df_ids(ctx.embedded_dataframes, ctx.history_blocks, ctx.injected_dfs)
        rebind_plots_to_new_df(ctx.plot_specs, ctx.embedded_dataframes, ctx.message)
        plot_source_dataframes, overlap_errors = await _build_plot_source_dataframes(
            ctx.plot_specs,
            ctx.embedded_dataframes,
            ctx.history_blocks,
            project_dir_path=ctx.project_dir_path,
        )
        overlap_errors.extend(_drop_inaccurate_truncated_overlap_specs(
            ctx.plot_specs,
            plot_source_dataframes,
            ctx.history_blocks,
        ))
        apply_overlap_message_hints(
            ctx.plot_specs,
            ctx.message,
            ctx.history_blocks,
            current_dataframes=plot_source_dataframes,
        )
        overlap_errors.extend(materialize_direct_set_overlap_plot_dataframes(
            ctx.plot_specs,
            ctx.embedded_dataframes,
            ctx.history_blocks,
            current_dataframes=plot_source_dataframes,
        ))
        overlap_errors.extend(materialize_overlap_plot_dataframes(
            ctx.plot_specs,
            ctx.embedded_dataframes,
            ctx.history_blocks,
            current_dataframes=plot_source_dataframes,
        ))
        if overlap_errors:
            overlap_error_text = "\n".join(f"- {error}" for error in overlap_errors)
            if ctx.clean_markdown.strip():
                ctx.clean_markdown = (
                    f"{ctx.clean_markdown.rstrip()}\n\n"
                    f"Plot issues:\n{overlap_error_text}"
                )
            else:
                ctx.clean_markdown = f"Plot issues:\n{overlap_error_text}"

        # Update conv_state with newly-embedded DFs
        if ctx.embedded_dataframes:
            _max_new_id = 0
            _edf_label = ""
            _edf_rows = "?"
            for _val in ctx.embedded_dataframes.values():
                _meta = _val.get("metadata", {})
                _eid = _meta.get("df_id")
                if isinstance(_eid, int) and _eid > _max_new_id:
                    _max_new_id = _eid
                    _edf_label = _meta.get("label", "")
                    _edf_rows = _meta.get("row_count", "?")
            if _max_new_id > 0:
                _new_latest = f"DF{_max_new_id}"
                ctx.conv_state.latest_dataframe = _new_latest
                if not any(
                    k.startswith(_new_latest + " ") or k == _new_latest
                    for k in ctx.conv_state.known_dataframes
                ):
                    ctx.conv_state.known_dataframes.append(
                        f"{_new_latest} ({_edf_label}, {_edf_rows} rows)"
                    )

        post_df_assignment_plot_fallback(
            ctx.plot_specs, ctx.embedded_dataframes, ctx.message,
            _extract_plot_style_params,
        )
        plot_source_dataframes, overlap_errors = await _build_plot_source_dataframes(
            ctx.plot_specs,
            ctx.embedded_dataframes,
            ctx.history_blocks,
            project_dir_path=ctx.project_dir_path,
        )
        overlap_errors.extend(_drop_inaccurate_truncated_overlap_specs(
            ctx.plot_specs,
            plot_source_dataframes,
            ctx.history_blocks,
        ))
        apply_overlap_message_hints(
            ctx.plot_specs,
            ctx.message,
            ctx.history_blocks,
            current_dataframes=plot_source_dataframes,
        )
        overlap_errors.extend(materialize_direct_set_overlap_plot_dataframes(
            ctx.plot_specs,
            ctx.embedded_dataframes,
            ctx.history_blocks,
            current_dataframes=plot_source_dataframes,
        ))
        overlap_errors.extend(materialize_overlap_plot_dataframes(
            ctx.plot_specs,
            ctx.embedded_dataframes,
            ctx.history_blocks,
            current_dataframes=plot_source_dataframes,
        ))
        if overlap_errors:
            overlap_error_text = "\n".join(f"- {error}" for error in overlap_errors)
            if ctx.clean_markdown.strip():
                ctx.clean_markdown = (
                    f"{ctx.clean_markdown.rstrip()}\n\n"
                    f"Plot issues:\n{overlap_error_text}"
                )
            else:
                ctx.clean_markdown = f"Plot issues:\n{overlap_error_text}"
        ctx.plot_specs = deduplicate_plot_specs(ctx.plot_specs)
        ctx.plot_specs = collapse_competing_specs(
            ctx.plot_specs, ctx.message, _extract_plot_style_params,
        )

        # ── Token totals ──────────────────────────────────────────────
        _total_usage = {
            "prompt_tokens": ctx.think_usage["prompt_tokens"] + ctx.analyze_usage["prompt_tokens"],
            "completion_tokens": ctx.think_usage["completion_tokens"] + ctx.analyze_usage["completion_tokens"],
            "total_tokens": ctx.think_usage["total_tokens"] + ctx.analyze_usage["total_tokens"],
            "model": ctx.engine.model_name,
        }

        # ── AGENT_PLAN block ──────────────────────────────────────────
        _plan_payload = {
            "markdown": ctx.clean_markdown,
            "skill": ctx.active_skill,
            "model": ctx.engine.model_name,
            "tokens": _total_usage,
            "state": ctx.conv_state.to_dict(),
        }
        if ctx.embedded_dataframes:
            _plan_payload["_dataframes"] = ctx.embedded_dataframes
        if ctx.embedded_images:
            _plan_payload["_images"] = ctx.embedded_images
        if ctx.provenance:
            _plan_payload["_provenance"] = ctx.provenance
        if ctx.sync_run_uuid:
            _plan_payload["_sync_run_uuid"] = ctx.sync_run_uuid

        # Debug payload
        _debug = dict(ctx.inject_debug)
        _debug["has_injected_dfs"] = bool(ctx.injected_dfs)
        _debug["injected_previous_data"] = ctx.injected_previous_data
        _debug["auto_calls_count"] = len(ctx.auto_calls)
        if ctx.auto_calls:
            _debug["auto_calls"] = [str(c) for c in ctx.auto_calls[:10]]
        _debug["llm_tags_remaining"] = {
            "data_call": len(ctx.data_call_matches),
            "legacy_encode": len(ctx.legacy_encode_matches),
            "legacy_analysis": len(ctx.legacy_analysis_matches),
        }
        _debug["embedded_df_count"] = len(ctx.embedded_dataframes)
        _debug["active_skill"] = ctx.active_skill
        _debug["pre_llm_skill"] = ctx.pre_llm_skill
        _debug["auto_skill_detected"] = ctx.auto_skill
        _debug["requested_skill"] = ctx.skill
        _debug["llm_raw_response_preview"] = ctx.raw_response[:800] if ctx.raw_response else ""
        if ctx.output_violations:
            _debug["output_violations"] = ctx.output_violations
        _plan_payload["_debug"] = _debug

        agent_block = _create_block_internal(
            ctx.session, ctx.project_id, "AGENT_PLAN",
            _plan_payload, status="DONE", owner_id=ctx.user.id,
        )

        await save_conversation_message(
            ctx.session, ctx.project_id, ctx.user.id, "assistant",
            ctx.clean_markdown,
            token_data=_total_usage, model_name=ctx.engine.model_name,
        )

        # ── AGENT_PLOT blocks ─────────────────────────────────────────
        plot_blocks = []
        if ctx.plot_specs:
            _all_df_map = _build_df_map(ctx)
            _plots_by_df = defaultdict(list)
            for _ps in ctx.plot_specs:
                _df_id = _ps.get("df_id")
                if _df_id is None:
                    logger.warning("Skipping PLOT spec with no df_id", spec=_ps)
                    continue
                _plots_by_df[_df_id].append(_ps)

            for _df_key, _chart_group in _plots_by_df.items():
                _charts = []
                for _cs in _chart_group:
                    _entry = {
                        "type": _cs.get("type", "histogram"),
                        "df_id": _cs.get("df_id"),
                        "x": _cs.get("x"),
                        "y": _cs.get("y"),
                        "sets": _cs.get("sets"),
                        "color": _cs.get("color"),
                        "palette": _cs.get("palette"),
                        "title": _cs.get("title"),
                        "xlabel": _cs.get("xlabel") or _cs.get("x_label"),
                        "ylabel": _cs.get("ylabel") or _cs.get("y_label"),
                        "agg": _cs.get("agg"),
                        "max_intersections": _cs.get("max_intersections"),
                        "source_df_ids": _cs.get("source_df_ids"),
                        "source_labels": _cs.get("source_labels"),
                        "source_match_cols": _cs.get("source_match_cols"),
                        "match_key_column": _cs.get("match_key_column"),
                    }
                    _charts.append({k: v for k, v in _entry.items() if v is not None})

                _plot_block = _create_block_internal(
                    ctx.session, ctx.project_id, "AGENT_PLOT",
                    {"charts": _charts, "skill": ctx.active_skill, "model": ctx.engine.model_name},
                    status="DONE", owner_id=ctx.user.id,
                )
                plot_blocks.append(_plot_block)
                try:
                    auto_capture_plot(
                        ctx.session,
                        user_id=ctx.user.id,
                        project_id=ctx.project_id,
                        charts=_charts,
                        block_id=_plot_block.id,
                    )
                except Exception:
                    logger.warning("Failed to persist plot memory", block_id=_plot_block.id, exc_info=True)
                logger.info("Created AGENT_PLOT block",
                            block_id=_plot_block.id, chart_count=len(_charts), df_key=_df_key)

        # ── APPROVAL_GATE block ───────────────────────────────────────
        gate_block = None
        if ctx.needs_approval:
            gate_block = await _build_approval_gate(ctx)

        pending_action_block = None
        if ctx.pending_action_payloads:
            pending_action_block = _build_pending_action_block(ctx)

        if ctx.pending_action_source_block:
            _results = ctx.all_results.get("cortex", [])
            _has_error = any("error" in item for item in _results) if _results else False
            ctx.pending_action_source_block.status = "FAILED" if _has_error else "COMPLETED"
            ctx.session.commit()

        # Save user conversation message
        await save_conversation_message(ctx.session, ctx.project_id, ctx.user.id, "user", ctx.message)

        ctx.short_circuit({
            "status": "ok",
            "user_block": row_to_dict(ctx.user_block),
            "agent_block": row_to_dict(agent_block),
            "gate_block": row_to_dict(gate_block) if gate_block else None,
            "pending_action_block": row_to_dict(pending_action_block) if pending_action_block else None,
            "plot_blocks": [row_to_dict(pb) for pb in plot_blocks] if plot_blocks else None,
        })


register_stage(ResponseAssemblyStage())


# ── Helpers ────────────────────────────────────────────────────────────────

def _build_df_map(ctx: ChatContext) -> dict:
    """Build a map of df_id → {columns, data, metadata, label}."""
    _all = {}
    for _blk in ctx.history_blocks:
        if _blk.type == "AGENT_PLAN":
            _dfs = get_block_payload(_blk).get("_dataframes", {})
            for _name, _data in _dfs.items():
                _id = _data.get("metadata", {}).get("df_id")
                if _id is not None:
                    _all[_id] = {**_data, "label": _name}
    for _name, _data in ctx.embedded_dataframes.items():
        _id = _data.get("metadata", {}).get("df_id")
        if _id is not None:
            _all[_id] = {**_data, "label": _name}
    return _all


async def _build_approval_gate(ctx: ChatContext):
    """Create the APPROVAL_GATE block."""
    gate_action = "download" if ctx.active_skill == "download_files" else "job"
    gate_skill = ctx.active_skill

    if gate_action == "download" and ctx.pending_download_files:
        _total_bytes = sum(f.get("size_bytes") or 0 for f in ctx.pending_download_files)
        from cortex.user_jail import get_user_data_dir
        _dl_target = str(get_user_data_dir(ctx.user.username))
        extracted_params = {
            "gate_action": "download",
            "files": ctx.pending_download_files,
            "total_size_bytes": _total_bytes,
            "target_dir": _dl_target,
        }
    else:
        if (ctx.remote_stage_approval_context
                and isinstance(ctx.remote_stage_approval_context.get("params"), dict)):
            extracted_params = dict(ctx.remote_stage_approval_context.get("params") or {})
        else:
            extracted_params = await job_parameters.extract_job_parameters_from_conversation(
                ctx.session, ctx.project_id,
            )
        if isinstance(extracted_params, dict):
            gate_action = extracted_params.get("gate_action") or gate_action
            if (gate_action == "remote_stage"
                    or (extracted_params.get("remote_action") or "").strip().lower() == "stage_only"):
                gate_action = "remote_stage"
                gate_skill = "remote_execution"
        if (extracted_params.get("execution_mode") or "local") == "slurm":
            gate_skill = "remote_execution"

    return _create_block_internal(
        ctx.session, ctx.project_id, "APPROVAL_GATE",
        {
            "label": "Do you authorize the Agent to proceed with this plan?",
            "extracted_params": extracted_params,
            "cache_preflight": extracted_params.get("cache_preflight") if isinstance(extracted_params, dict) else None,
            "gate_action": gate_action,
            "attempt_number": 1,
            "rejection_history": [],
            "skill": gate_skill,
            "model": ctx.engine.model_name,
        },
        status="PENDING",
        owner_id=ctx.user.id,
    )


def _build_pending_action_block(ctx: ChatContext):
    action = ctx.pending_action_payloads[0]
    summary = action.get("summary") or summarize_dataframe_action(
        action.get("tool", ""), action.get("params", {}),
    )
    payload = {
        "summary": summary,
        "action_call": {
            "source_type": action.get("source_type", "service"),
            "source_key": action.get("source_key", "cortex"),
            "tool": action.get("tool"),
            "params": action.get("params", {}),
        },
        "confirmation_markdown": f"{summary}. Proceeding with the saved dataframe action.",
        "skill": ctx.active_skill,
        "model": ctx.engine.model_name,
    }
    return _create_block_internal(
        ctx.session,
        ctx.project_id,
        "PENDING_ACTION",
        payload,
        status="PENDING",
        owner_id=ctx.user.id,
    )
