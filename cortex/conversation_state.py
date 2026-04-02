"""
Conversation state reconstruction — extracted from cortex/app.py (Tier 2).

Two functions:
  _build_conversation_state  — build a ConversationState from blocks/history
  _extract_job_context_from_history — scan blocks/history for job context (work_dir, run_uuid)
"""

import re

from cortex.llm_validators import get_block_payload


# ---------------------------------------------------------------------------
# _build_conversation_state
# ---------------------------------------------------------------------------

def _build_conversation_state(
    active_skill: str,
    conversation_history: list | None,
    history_blocks: list | None = None,
    project_id: str | None = None,
    db=None,
    user_id: str | None = None,
) -> "ConversationState":
    """
    Build a structured ConversationState from blocks and conversation history.

    Fast path: if the last AGENT_PLAN block has a "state" key, use it directly.
    Slow path: scan all blocks to reconstruct state (same logic as the old
    _extract_job_context_from_history + parameter extraction).
    """
    from cortex.schemas import ConversationState

    # Fast path: check last AGENT_PLAN block for cached state
    if history_blocks:
        for blk in reversed(history_blocks):
            if blk.type == "AGENT_PLAN":
                _pl = get_block_payload(blk)
                cached = _pl.get("state")
                if cached and isinstance(cached, dict):
                    state = ConversationState.from_dict(cached)
                    state.active_skill = active_skill
                    # The cached state was saved *before* the block's own DFs
                    # were embedded, so latest_dataframe may be stale.  Patch it
                    # by scanning the same block's embedded dataframes.
                    _block_dfs = _pl.get("_dataframes", {})
                    _max_id = 0
                    for _bdf_val in _block_dfs.values():
                        _bdf_meta = _bdf_val.get("metadata", {})
                        _bdf_id = _bdf_meta.get("df_id")
                        if isinstance(_bdf_id, int) and _bdf_id > _max_id:
                            _max_id = _bdf_id
                    if _max_id > 0:
                        _patched = f"DF{_max_id}"
                        if state.latest_dataframe != _patched:
                            state.latest_dataframe = _patched
                            # Also append to known_dataframes if missing
                            if not any(k.startswith(_patched + " ") or k == _patched
                                       for k in state.known_dataframes):
                                _bdf_label = ""
                                _bdf_rows = "?"
                                for _v in _block_dfs.values():
                                    _m = _v.get("metadata", {})
                                    if _m.get("df_id") == _max_id:
                                        _bdf_label = _m.get("label", "")
                                        _bdf_rows = _m.get("row_count", "?")
                                        break
                                state.known_dataframes.append(
                                    f"{_patched} ({_bdf_label}, {_bdf_rows} rows)"
                                )
                    # Fast-path fix: re-extract collected_params from the LATEST
                    # user messages so they reflect the current request, not a
                    # stale cached state from a previous submission cycle.
                    if conversation_history and active_skill == "analyze_local_sample":
                        _sample_re2 = re.compile(r'(?:called?|named?|sample[_ ]?name)\s+["\']?(\w+)', re.I)
                        _path_re2 = re.compile(r'(?:path|directory|folder)\s*[:=]?\s*(/\S+)', re.I)
                        _type_re2 = re.compile(r'\b(cdna|c-dna|cDNA|rna|dna)\b', re.I)
                        _genome_re2 = re.compile(r'\b(GRCh38|mm39|hg38|mm10)\b', re.I)
                        _fresh_params: dict[str, str] = {}
                        for _msg in reversed(conversation_history):
                            if _msg.get("role") != "user":
                                continue
                            _cnt = _msg.get("content", "")
                            if "sample_name" not in _fresh_params:
                                _sm = _sample_re2.search(_cnt)
                                if _sm:
                                    _fresh_params["sample_name"] = _sm.group(1)
                            if "path" not in _fresh_params:
                                _pm = _path_re2.search(_cnt)
                                if _pm:
                                    _fresh_params["path"] = _pm.group(1)
                            if "sample_type" not in _fresh_params:
                                _tm = _type_re2.search(_cnt)
                                if _tm:
                                    _fresh_params["sample_type"] = _tm.group(1).upper()
                            if "reference_genome" not in _fresh_params:
                                _gm = _genome_re2.search(_cnt)
                                if _gm:
                                    _fresh_params["reference_genome"] = _gm.group(1)
                        if _fresh_params:
                            state.collected_params.update(_fresh_params)
                            # Also update top-level sample_name if overridden
                            if "sample_name" in _fresh_params:
                                state.sample_name = _fresh_params["sample_name"]
                    return state
                break  # only check the most recent AGENT_PLAN

    # Slow path: reconstruct from all blocks
    state = ConversationState(active_skill=active_skill, active_project=project_id)

    # --- Extract workflows from EXECUTION_JOB blocks ---
    if history_blocks:
        for blk in history_blocks:
            if blk.type != "EXECUTION_JOB":
                continue
            _pl = get_block_payload(blk)
            _wd = _pl.get("work_directory", "")
            _uuid = _pl.get("run_uuid", "")
            if _wd or _uuid:
                state.workflows.append({
                    "work_dir": _wd,
                    "sample_name": _pl.get("sample_name", ""),
                    "mode": _pl.get("mode", ""),
                    "run_uuid": _uuid,
                })
        if state.workflows:
            latest = state.workflows[-1]
            state.work_dir = latest.get("work_dir")
            state.sample_name = latest.get("sample_name")
            state.sample_type = latest.get("mode")
            state.active_workflow_index = len(state.workflows) - 1

    # --- Extract active plan from WORKFLOW_PLAN blocks ---
    if history_blocks:
        for blk in reversed(history_blocks):
            if blk.type == "WORKFLOW_PLAN":
                _pl = get_block_payload(blk)
                if _pl.get("status") not in ("COMPLETED", "FAILED"):
                    state.active_plan_id = blk.id
                    state.active_plan_step = _pl.get("current_step_id")
                    break

    # --- Extract ENCSR/ENCFF accessions from conversation ---
    if conversation_history:
        _encsr_pattern = re.compile(r'ENCSR[0-9A-Z]{6}')
        _encff_pattern = re.compile(r'ENCFF[0-9A-Z]{6}')
        for msg in reversed(conversation_history):
            content = msg.get("content", "")
            if not state.active_experiment:
                _m = _encsr_pattern.search(content)
                if _m:
                    state.active_experiment = _m.group()
            if not state.active_file:
                _m = _encff_pattern.search(content)
                if _m:
                    state.active_file = _m.group()
            if state.active_experiment and state.active_file:
                break

    # --- Extract known dataframes from AGENT_PLAN blocks ---
    if history_blocks:
        for blk in history_blocks:
            if blk.type != "AGENT_PLAN":
                continue
            _pl = get_block_payload(blk)
            _dfs = _pl.get("_dataframes", {})
            for _df_key, _df_val in _dfs.items():
                _meta = _df_val.get("metadata", {})
                _df_id = _meta.get("df_id")
                _label = _meta.get("label", _df_key)
                _row_count = _meta.get("row_count", "?")
                if _df_id and _meta.get("visible", True):
                    state.known_dataframes.append(f"DF{_df_id} ({_label}, {_row_count} rows)")
        # The latest DF is the highest-numbered one
        if state.known_dataframes:
            state.latest_dataframe = state.known_dataframes[-1].split(" ")[0]  # e.g. "DF8"

    # --- Append remembered dataframe memories ---
    if db is not None and user_id is not None:
        from cortex.memory_service import get_remembered_df_map
        _remembered = get_remembered_df_map(db, user_id, project_id)
        for _r_key in sorted(_remembered, key=lambda k: (isinstance(k, int), k)):
            _rd = _remembered[_r_key]
            # Named DFs use their name; unnamed ones use DF<n>
            _key_label = _r_key if isinstance(_r_key, str) else f"DF{_r_key}"
            state.known_dataframes.append(
                f"{_key_label} ({_rd['label']}, {_rd['row_count']} rows)"
            )

    # --- Extract collected parameters (for analyze_local_sample) ---
    # Iterate in REVERSE so the most-recent user message wins when multiple
    # messages mention different sample names (e.g. C2C12r1 then C2C12r2).
    if conversation_history and active_skill == "analyze_local_sample":
        _sample_re = re.compile(r'(?:called?|named?|sample[_ ]?name)\s+["\']?(\w+)', re.I)
        _path_re = re.compile(r'(?:path|directory|folder)\s*[:=]?\s*(/\S+)', re.I)
        _type_re = re.compile(r'\b(cdna|c-dna|cDNA|rna|dna)\b', re.I)
        _genome_re = re.compile(r'\b(GRCh38|mm39|hg38|mm10)\b', re.I)
        for msg in reversed(conversation_history):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if "sample_name" not in state.collected_params:
                _m = _sample_re.search(content)
                if _m:
                    state.collected_params["sample_name"] = _m.group(1)
            if "path" not in state.collected_params:
                _m = _path_re.search(content)
                if _m:
                    state.collected_params["path"] = _m.group(1)
            if "sample_type" not in state.collected_params:
                _m = _type_re.search(content)
                if _m:
                    state.collected_params["sample_type"] = _m.group(1).upper()
            if "reference_genome" not in state.collected_params:
                _m = _genome_re.search(content)
                if _m:
                    state.collected_params["reference_genome"] = _m.group(1)

    return state


# ---------------------------------------------------------------------------
# _extract_job_context_from_history
# ---------------------------------------------------------------------------

def _extract_job_context_from_history(
    conversation_history: list | None,
    history_blocks: list | None = None,
) -> dict:
    """
    Scan conversation / block history for job context.

    Returns a dict with:
      - 'work_dir'  : str — work directory of the *most recent* job
      - 'run_uuid'  : str — run UUID of the most recent job (internal only)
      - 'workflows' : list[dict] — all workflows in the project
           Each dict: {work_dir, sample_name, mode, run_uuid}
    """
    context: dict = {}

    # --- Primary source: EXECUTION_JOB blocks (authoritative) -----------
    workflows: list[dict] = []
    if history_blocks:
        for blk in history_blocks:
            if blk.type != "EXECUTION_JOB":
                continue
            _pl = get_block_payload(blk)
            _wd = _pl.get("work_directory", "")
            _uuid = _pl.get("run_uuid", "")
            if _wd or _uuid:
                workflows.append({
                    "work_dir": _wd,
                    "sample_name": _pl.get("sample_name", ""),
                    "mode": _pl.get("mode", ""),
                    "run_uuid": _uuid,
                })
        if workflows:
            latest = workflows[-1]
            context["work_dir"] = latest["work_dir"]
            context["run_uuid"] = latest["run_uuid"]
            context["workflows"] = workflows
            return context

    # --- Fallback: parse conversation text for UUID/work_dir (legacy) ----
    if not conversation_history:
        return context

    uuid_pattern = r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'
    for msg in reversed(conversation_history):
        content = msg.get("content", "")
        if not content:
            continue
        if "run_uuid" not in context:
            explicit_match = re.search(
                r'(?:use UUID|Run UUID|run_uuid|UUID)[:\s=]+\s*(' + uuid_pattern + r')',
                content, re.IGNORECASE
            )
            if explicit_match:
                context["run_uuid"] = explicit_match.group(1).lower()
        if "work_dir" not in context:
            work_dir_match = re.search(r'Work Directory:\s*(\S+)', content)
            if work_dir_match:
                context["work_dir"] = work_dir_match.group(1).strip()
        if "run_uuid" in context and "work_dir" in context:
            break
    if "run_uuid" not in context:
        for msg in reversed(conversation_history):
            content = msg.get("content", "")
            uuid_matches = re.findall(uuid_pattern, content, re.IGNORECASE)
            if uuid_matches:
                context["run_uuid"] = uuid_matches[-1].lower()
                break
    return context
