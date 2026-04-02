"""
Context injection — extracted from cortex/app.py (Tier 2).

_inject_job_context augments the user message with conversational context
so the LLM can maintain continuity across turns.  Covers Dogme workflow
injection, local-sample parameter collection, and ENCODE follow-up DF injection.
"""

import re

from cortex.conversation_state import _extract_job_context_from_history
from cortex.encode_helpers import (
    _ENCODE_ASSAY_ALIASES,
    _extract_encode_search_term,
    _looks_like_assay,
)
from cortex.llm_validators import get_block_payload
from common.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# _inject_job_context
# ---------------------------------------------------------------------------

def _inject_job_context(user_message: str, active_skill: str,
                        conversation_history: list | None,
                        history_blocks: list | None = None) -> tuple[str, dict, dict]:
    """
    Inject relevant conversational context into the user message so the LLM
    can maintain continuity without having to parse conversation history itself.

    Returns:
        (augmented_message, injected_dataframes, debug_info)
        injected_dataframes is a dict suitable for merging into _embedded_dataframes.
        It contains the server-side filtered subset so the UI can render it.
        debug_info is a dict of diagnostic data for the UI debug panel.

    Covers:
    - Dogme skills: inject UUID and work directory
    - ENCODE skills: inject previous dataframe rows for follow-up filter questions
    """
    if not conversation_history:
        return user_message, {}, {}

    # --- Dogme skills: inject workflow directory paths ---
    dogme_skills = {"run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
                    "analyze_job_results"}
    if active_skill in dogme_skills:
        context = _extract_job_context_from_history(
            conversation_history, history_blocks=history_blocks
        )
        workflows = context.get("workflows", [])

        # Build [CONTEXT] line(s) — list ALL workflows so the LLM can
        # reference files from any workflow in the project.
        parts = []
        if workflows:
            if len(workflows) == 1:
                wf = workflows[0]
                parts.append(f"work_dir={wf['work_dir']}")
                if wf.get("sample_name"):
                    parts.append(f"sample={wf['sample_name']}")
            else:
                # Multiple workflows — enumerate them
                wf_lines = []
                for i, wf in enumerate(workflows, 1):
                    _folder = wf["work_dir"].rstrip("/").rsplit("/", 1)[-1] if wf["work_dir"] else f"workflow{i}"
                    _label = f"{_folder} (sample={wf.get('sample_name', '?')}, mode={wf.get('mode', '?')}): work_dir={wf['work_dir']}"
                    wf_lines.append(_label)
                parts.append("workflows=[\n  " + "\n  ".join(wf_lines) + "\n]")
                # Also note which one is the most recent / active
                latest = workflows[-1]
                _latest_folder = latest["work_dir"].rstrip("/").rsplit("/", 1)[-1] if latest["work_dir"] else "?"
                parts.append(f"active_workflow={_latest_folder}")
        elif context.get("work_dir"):
            # Fallback: single work_dir from conversation text
            parts.append(f"work_dir={context['work_dir']}")
        elif context.get("run_uuid"):
            # Legacy fallback
            parts.append(f"run_uuid={context['run_uuid']}")

        # Check for an explicit DF reference (e.g. "plot a histogram of DF1").
        # If found, look up the DataFrame from history and inject its metadata so
        # the LLM knows DF<N> is an in-memory table — NOT a file to look up.
        _df_ref_match = re.search(r'\bDF\s*(\d+)\b', user_message, re.IGNORECASE)
        _df_note = ""
        # Also detect implicit DF reference via plot/viz keywords ("plot this")
        _viz_keywords = {"plot", "chart", "graph", "histogram", "scatter",
                         "pie", "heatmap", "visualize", "bar chart", "box plot"}
        _implicit_df_ref = (
            not _df_ref_match
            and any(kw in user_message.lower() for kw in _viz_keywords)
        )
        _tgt_df_id = None
        if _df_ref_match:
            _tgt_df_id = int(_df_ref_match.group(1))
        elif _implicit_df_ref and history_blocks:
            # Find the highest DF ID across all history blocks
            for _hblk in reversed(history_blocks):
                if _hblk.type != "AGENT_PLAN":
                    continue
                _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                for _dfd in _hblk_dfs.values():
                    _m = _dfd.get("metadata", {})
                    _did = _m.get("df_id")
                    if isinstance(_did, int):
                        if _tgt_df_id is None or _did > _tgt_df_id:
                            _tgt_df_id = _did
                if _tgt_df_id is not None:
                    break  # found DFs in the most recent AGENT_PLAN

        if _tgt_df_id is not None and history_blocks:
            for _hblk in reversed(history_blocks):
                if _hblk.type != "AGENT_PLAN":
                    continue
                _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                for _dfd in _hblk_dfs.values():
                    _m = _dfd.get("metadata", {})
                    if _m.get("df_id") == _tgt_df_id:
                        _cols = _dfd.get("columns", [])
                        _nrows = len(_dfd.get("data", []))
                        _label = _m.get("label", f"DF{_tgt_df_id}")
                        _df_note = (
                            f"\n[NOTE: DF{_tgt_df_id} is an in-memory DataFrame from this "
                            f"conversation — it is NOT a file or run result to look up. "
                            f"Label: '{_label}'. Columns: {_cols}. Rows: {_nrows}. "
                            f"To visualize it use [[PLOT:...]] tags. "
                            f"Do NOT call find_file, list_job_files, or any analyzer tool for this.]"
                        )
                        break
                if _df_note:
                    break

        context_line = f"[CONTEXT: {', '.join(parts)}]" if parts else ""
        augmented = "\n".join(filter(None, [context_line, user_message])) + _df_note
        return augmented, {}, {"skill": active_skill, "context": "dogme",
                               "df_note_injected": bool(_df_note)}

    # --- Local sample intake: inject already-collected parameters ---
    # Weak models lose track of parameters gathered across turns when the
    # system prompt is large.  Scan the conversation so far and inject a
    # concise [CONTEXT] line so the LLM doesn't have to re-parse history.
    if active_skill == "analyze_local_sample":
        _collected: dict[str, str] = {}
        # Field names MUST match the skill doc (skills/analyze_local_sample/SKILL.md):
        #   sample_name, path, sample_type, reference_genome
        _field_patterns = {
            "sample_name": re.compile(
                r'(?:sample\s*name)[:\s*]+([^\n*,|]+)', re.IGNORECASE),
            "path": re.compile(
                r'(?:data\s*path|path|directory)[:\s*]+(/[^\n*,]+)', re.IGNORECASE),
            "sample_type": re.compile(
                r'(?:data\s*type|sample\s*type|type|mode)[:\s*]+'
                r'(DNA|RNA|CDNA|cDNA|Fiber-seq|Fiberseq)', re.IGNORECASE),
            "reference_genome": re.compile(
                r'(?:reference\s*genome|genome)[:\s*]+'
                r'(GRCh38|mm39|mm10|hg38|T2T-CHM13)', re.IGNORECASE),
        }

        # First pass: extract from the original user request and any assistant
        # summaries already in conversation_history.
        for msg in conversation_history:
            content = msg.get("content", "")
            for field, pat in _field_patterns.items():
                m = pat.search(content)
                if m:
                    _collected[field] = m.group(1).strip().rstrip("*").strip()

        # Heuristic: detect sample_type from keywords in ALL user messages
        # AND the current message (which hasn't been appended to history yet).
        # ALWAYS run this — user messages are the source of truth for sample_type,
        # not assistant echoes (which may have gotten it wrong, e.g. "DNA" for CDNA).
        _st_from_user: str | None = None
        _all_user_texts = [m.get("content", "") for m in conversation_history
                           if m.get("role") == "user"]
        _all_user_texts.append(user_message)  # current turn
        for _ut in _all_user_texts:
            _fl = _ut.lower()
            if "cdna" in _fl or "c-dna" in _fl:
                _st_from_user = "CDNA"; break
            elif "rna" in _fl and "cdna" not in _fl:
                _st_from_user = "RNA"; break
            elif "fiber" in _fl:
                _st_from_user = "Fiber-seq"; break
            elif "dna" in _fl:
                _st_from_user = "DNA"; break
        if _st_from_user:
            _collected["sample_type"] = _st_from_user

        # Heuristic: extract sample_name from phrasing like "called <name>"
        if "sample_name" not in _collected:
            for msg in conversation_history:
                if msg.get("role") != "user":
                    continue
                _called_m = re.search(
                    r'(?:called|named|name(?:d)?)\s+(\S+)', msg["content"], re.IGNORECASE)
                if _called_m:
                    _collected["sample_name"] = _called_m.group(1).strip().rstrip(".,;:")
                    break
            # Also check current message (first turn won't be in history yet)
            if "sample_name" not in _collected:
                _called_m = re.search(
                    r'(?:called|named|name(?:d)?)\s+(\S+)', user_message, re.IGNORECASE)
                if _called_m:
                    _collected["sample_name"] = _called_m.group(1).strip().rstrip(".,;:")

        # Heuristic: extract path from user messages containing absolute paths
        if "path" not in _collected:
            for msg in conversation_history:
                if msg.get("role") != "user":
                    continue
                _path_m = re.search(r'(/[^\s,;:*?"<>|]+)', msg["content"])
                if _path_m:
                    _collected["path"] = _path_m.group(1).strip()
                    break
            # Also check current message
            if "path" not in _collected:
                _path_m = re.search(r'(/[^\s,;:*?"<>|]+)', user_message)
                if _path_m:
                    _collected["path"] = _path_m.group(1).strip()

        # Heuristic: detect reference_genome from short user reply like "mm39"
        if "reference_genome" not in _collected:
            for msg in conversation_history:
                if msg.get("role") != "user":
                    continue
                _genome_m = re.match(
                    r'^\s*(GRCh38|mm39|mm10|hg38)\s*$', msg["content"], re.IGNORECASE)
                if _genome_m:
                    _collected["reference_genome"] = _genome_m.group(1).strip()

        # Also check current message for a genome answer
        _cur_genome_m = re.match(
            r'^\s*(GRCh38|mm39|mm10|hg38)\s*$', user_message, re.IGNORECASE)
        if _cur_genome_m:
            _collected["reference_genome"] = _cur_genome_m.group(1).strip()

        # Heuristic: infer reference_genome from organism keywords
        if "reference_genome" not in _collected:
            _all_text = " ".join(
                m.get("content", "") for m in conversation_history
                if m.get("role") == "user"
            ) + " " + user_message
            _all_lower = _all_text.lower()
            if "mouse" in _all_lower or "mus musculus" in _all_lower:
                _collected["reference_genome"] = "mm39"
            elif "human" in _all_lower or "homo sapiens" in _all_lower:
                _collected["reference_genome"] = "GRCh38"

        if _collected:
            _parts = []
            for k, v in _collected.items():
                _parts.append(f"{k}={v}")

            # If all 4 fields are collected, give the LLM an unambiguous
            # directive so it doesn't misread e.g. "CDNA" as "DNA".
            _required = {"sample_name", "path", "sample_type", "reference_genome"}
            if _required.issubset(_collected.keys()):
                context_line = (
                    f"[CONTEXT: ALL 4 parameters are collected — go straight to "
                    f"the approval summary. Use these EXACT values:\n"
                    f"  sample_name={_collected['sample_name']}\n"
                    f"  path={_collected['path']}\n"
                    f"  sample_type={_collected['sample_type']}\n"
                    f"  reference_genome={_collected['reference_genome']}\n"
                    f"Show the summary with these values and include [[APPROVAL_NEEDED]]. "
                    f"The pipeline is Dogme {_collected['sample_type']}.]"
                )
            else:
                context_line = (
                    f"[CONTEXT: Parameters already collected from this conversation: "
                    f"{', '.join(_parts)}. "
                    f"Do NOT re-ask for these. Only ask for fields still missing.]"
                )
            augmented = f"{context_line}\n{user_message}"
            return augmented, {}, {"skill": active_skill, "context": "local_sample_intake",
                                   "collected_params": _collected}

        return user_message, {}, {"skill": active_skill, "context": "local_sample_intake",
                                  "collected_params": {}}

    # --- ENCODE skills: inject previous search context for follow-ups ---
    encode_skills = {"ENCODE_Search", "ENCODE_LongRead"}
    if active_skill in encode_skills:
        msg_lower = user_message.lower()

        # Browsing commands ("list files", "list workflows") should NOT inject
        # previous ENCODE dataframes — they route to the analyzer, not ENCODE.
        _browsing_cmd_patterns = [
            r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
            r'\b(?:list|show)\s+(?:the\s+)?files?\b',
        ]
        if any(re.search(p, msg_lower) for p in _browsing_cmd_patterns):
            return user_message, {}, {"skill": active_skill, "context": "browsing_command"}

        # Check if current message already has an explicit accession
        has_accession = bool(re.findall(r'ENC[A-Z]{2}\d{3}[A-Z]{3}', user_message, re.IGNORECASE))

        # Detect if the message is a NEW search query (not a follow-up on
        # existing data).  We use positive signals for "new query" rather
        # than trying to enumerate every biosample — that doesn't scale.
        #
        # A message is a NEW query if it:
        #   a) Contains explicit new-query patterns, OR
        #   b) Mentions a term that wasn't in the previous results.
        #
        # A message is a FOLLOW-UP if it:
        #   - Asks about data already on screen ("how many of them are RNA-seq?")
        #   - References a DF ("show me DF1")
        #   - Uses referential words with no new subject ("which are methylated?")

        # Positive signals for a NEW independent query
        _new_query_patterns = [
            r'how many\s+\S+.*experiments?',      # "how many X experiments"
            r'search\s+(?:encode\s+)?for\s+\S+',  # "search encode for X"
            r'does\s+encode\s+have\s+\S+',         # "does encode have X"
            r'\S+\s+experiments?\s+(?:in|on|from)\s+encode',  # "X experiments in encode"
            r'(?:find|list|show|get)\s+(?:all\s+)?\S+\s+experiments?',  # "list X experiments"
        ]
        _is_new_query = any(re.search(p, msg_lower) for p in _new_query_patterns)

        # Referential language is a strong signal for follow-up —
        # the user is talking about data already on screen.
        _followup_signals = [
            r'\bof\s+them\b',          # "how many of them"
            r'\bthose\b',               # "show those"
            r'\bthese\b',               # "filter these"
            r'\bthe\s+results?\b',     # "the results"
            r'\bthe\s+data\b',         # "the data"
            r'\bthe\s+accessions?\b',  # "the accessions"
            r'\bthe\s+samples?\b',     # "the samples"
            r'\bthe\s+experiments?\b', # "the experiments"
            r'\bwhich\s+(?:are|is|were|have)\b', # "which are long read..."
            r'\bfrom\s+(?:the\s+)?(?:previous|last|above)\b',
            r'\bDF\s*\d+\b',          # explicit DF reference
            r'\bamong\s+them\b',       # "among them"
            r'\bof\s+those\b',         # "of those"
        ]
        _is_followup = any(re.search(p, msg_lower) for p in _followup_signals)
        if _is_followup:
            _is_new_query = False  # override — referential language wins

        # Also check: does the message mention a term that is NOT present in
        # any previous dataframe labels?  If so, it's a new search subject.
        # BUT: assay names ("long read RNA-seq", "ATAC-seq", etc.) are NOT
        # new search subjects — they're filters on existing data.
        if not _is_new_query and not has_accession and not _is_followup:
            _extracted_term = _extract_encode_search_term(user_message)
            if _extracted_term and not _looks_like_assay(_extracted_term):
                _prev_labels_lower = set()
                if history_blocks:
                    for _hblk in reversed(history_blocks[-4:]):
                        if _hblk.type == "AGENT_PLAN":
                            _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                            for _dn in _hblk_dfs:
                                _prev_labels_lower.add(_dn.lower())
                # If the extracted term doesn't appear in any previous DF label,
                # it's a new subject
                _term_lower = _extracted_term.lower()
                if _prev_labels_lower and not any(
                    _term_lower in lbl for lbl in _prev_labels_lower
                ):
                    _is_new_query = True
                    logger.info(
                        "Detected new ENCODE search subject (not in prev DFs)",
                        term=_extracted_term, prev_labels=_prev_labels_lower,
                    )
            elif _extracted_term and _looks_like_assay(_extracted_term):
                logger.info(
                    "Extracted term looks like an assay — treating as follow-up filter, not new query",
                    term=_extracted_term,
                )

        if not has_accession and not _is_new_query:
            # No new subject in message — this is a follow-up question.
            # PREFERRED: inject dataframe rows from the most recent AGENT_PLAN
            # block that has _dataframes — this gives the LLM the full, accurate
            # tabular data rather than the potentially truncated <details> text.
            # Detect assay filter in the current message so we can
            # pre-filter dataframe rows server-side (reliable) instead of
            # asking the LLM to filter a large table (unreliable).
            _msg_lower_enc = user_message.lower()
            _assay_filter: str | None = None
            for _alias, _canonical in _ENCODE_ASSAY_ALIASES.items():
                if re.search(r'\b' + re.escape(_alias) + r'\b', _msg_lower_enc):
                    _assay_filter = _canonical
                    break

            # Detect output_type filter (for file follow-ups like
            # "which are methylated reads?", "show me unfiltered alignments")
            _output_type_filter: str | None = None
            _output_type_aliases: dict[str, str] = {
                "methylated reads": "methylated reads",
                "unfiltered alignments": "unfiltered alignments",
                "filtered alignments": "filtered alignments",
                "alignments": "alignments",
                "signal p-value": "signal p-value",
                "fold change over control": "fold change over control",
                "peaks": "peaks",
                "conservative idr thresholded peaks": "conservative IDR thresholded peaks",
                "optimal idr thresholded peaks": "optimal IDR thresholded peaks",
                "transcriptome alignments": "transcriptome alignments",
                "gene quantifications": "gene quantifications",
                "transcript quantifications": "transcript quantifications",
                "reads": "reads",
            }
            if not _assay_filter:  # only look for output_type if no assay matched
                for _ot_alias, _ot_canonical in _output_type_aliases.items():
                    if re.search(r'\b' + re.escape(_ot_alias) + r'\b', _msg_lower_enc):
                        _output_type_filter = _ot_canonical
                        break

            # Detect file-type context from CURRENT message only.
            # e.g. "show me the bed files" → inject bed df only.
            # For follow-ups like "which of them are methylated reads?"
            # (no file type in message), the last-visible-DF logic below
            # selects the right dataframe automatically.
            _known_file_types = {"bam", "fastq", "fastq.gz", "bed", "bigwig", "bigbed",
                                 "tsv", "csv", "gtf", "txt", "hic"}
            _file_type_filter: str | None = None
            for _ft in _known_file_types:
                if re.search(r'\b' + re.escape(_ft) + r'\b', _msg_lower_enc):
                    _file_type_filter = _ft
                    break

            # Check for explicit DF reference (e.g. "DF3", "df 3").
            # If found, use that specific dataframe instead of guessing.
            _target_df_id: int | None = None
            _df_ref_match = re.search(r'\bDF\s*(\d+)\b', user_message, re.IGNORECASE)
            if _df_ref_match:
                _target_df_id = int(_df_ref_match.group(1))

            # If no explicit DF reference and no file-type keyword in the
            # CURRENT message, default to the most recent *visible* DF.
            # "them" / "those" / "which of them" refers to the last table
            # the user actually saw.
            if _target_df_id is None and not _file_type_filter and history_blocks:
                _best_visible_id: int | None = None
                for _hblk in reversed(history_blocks):
                    if _hblk.type != "AGENT_PLAN":
                        continue
                    _hblk_dfs = get_block_payload(_hblk).get("_dataframes", {})
                    for _dfd in _hblk_dfs.values():
                        _m = _dfd.get("metadata", {})
                        _did = _m.get("df_id")
                        if _did is not None and _m.get("visible", False):
                            if _best_visible_id is None or _did > _best_visible_id:
                                _best_visible_id = _did
                    if _best_visible_id is not None:
                        break  # only check the most recent block with dfs
                if _best_visible_id is not None:
                    _target_df_id = _best_visible_id
                    logger.info(
                        "Auto-selected last visible DF for follow-up",
                        df_id=_target_df_id,
                    )
                else:
                    logger.info(
                        "No visible DF found in history for auto-selection",
                        blocks_checked=sum(1 for b in history_blocks if b.type == "AGENT_PLAN"),
                    )

            logger.info(
                "_inject_job_context ENCODE follow-up",
                target_df_id=_target_df_id,
                file_type_filter=_file_type_filter,
                assay_filter=_assay_filter,
                output_type_filter=_output_type_filter,
            )

            if history_blocks:
                # Scan ALL history blocks (not just recent ones) for DF data.
                # When user references "DF1" explicitly, that DF may be from
                # much earlier in the conversation.
                # For heuristic (no explicit DF ref), we stop at the first
                # block with matching dataframes (most recent).
                table_sections: list[str] = []
                _injected_dfs: dict = {}
                for blk in reversed(history_blocks):
                    if blk.type != "AGENT_PLAN":
                        continue
                    blk_payload = get_block_payload(blk)
                    dfs = blk_payload.get("_dataframes")
                    if not dfs:
                        continue
                    _found_in_block = False
                    for df_name, df_data in dfs.items():
                        rows = df_data.get("data", [])
                        cols = df_data.get("columns", [])
                        if not rows or not cols:
                            continue

                        # DataFrame selection: by explicit DF reference OR
                        # by file-type filter heuristic.
                        _df_meta = df_data.get("metadata", {})
                        _df_id = _df_meta.get("df_id")

                        if _target_df_id is not None:
                            # User referenced a specific DF — skip all others.
                            if _df_id != _target_df_id:
                                continue
                        else:
                            # Heuristic: file-type context from message/history.
                            _df_file_type = _df_meta.get("file_type", "")
                            if _file_type_filter and _df_file_type:
                                if _df_file_type.lower() != _file_type_filter.lower():
                                    continue
                            elif _file_type_filter and not _df_file_type:
                                if _file_type_filter not in df_name.lower():
                                    continue

                        # Determine filter columns
                        _assay_col = next(
                            (c for c in cols if c.lower() in ("assay", "assay type", "assay_type")),
                            None
                        )
                        _output_type_col = next(
                            (c for c in cols if c.lower() in ("output type", "output_type")),
                            None
                        )

                        filtered_rows = rows
                        filter_desc = ""
                        if _assay_filter and _assay_col:
                            filtered_rows = [
                                r for r in rows
                                if _assay_filter.lower() in str(r.get(_assay_col, "")).lower()
                            ]
                            filter_desc = f" filtered to assay='{_assay_filter}'"
                            if not filtered_rows:
                                if _target_df_id is not None:
                                    # User explicitly referenced this DF — inject
                                    # the full data so the LLM can confirm 0 matches
                                    # rather than falling through to API calls.
                                    filtered_rows = rows
                                    filter_desc = (
                                        f" (0 rows match assay='{_assay_filter}'"
                                        f" — showing all {len(rows)} rows)"
                                    )
                                else:
                                    continue
                        elif _output_type_filter and _output_type_col:
                            filtered_rows = [
                                r for r in rows
                                if _output_type_filter.lower() in str(r.get(_output_type_col, "")).lower()
                            ]
                            filter_desc = f" filtered to output_type='{_output_type_filter}'"
                            if not filtered_rows:
                                # No exact match — inject all rows from this (already
                                # file-type-scoped) dataframe so the LLM can reason
                                # accurately rather than falling through to hallucination.
                                filtered_rows = rows
                                filter_desc = f" (user asked for output_type='{_output_type_filter}', answer from this data)"
                                logger.info(
                                    "output_type filter matched 0 rows; injecting full file-type df",
                                    df=df_name, output_type_filter=_output_type_filter,
                                )

                        # Cap at 500 rows for large unfiltered datasets
                        MAX_DF_ROWS = 500
                        shown = filtered_rows[:MAX_DF_ROWS]
                        capped = len(filtered_rows) > MAX_DF_ROWS
                        header = "| " + " | ".join(cols) + " |"
                        sep = "|" + "|".join(["---"] * len(cols)) + "|"
                        body_lines = [
                            "| " + " | ".join(str(row.get(c, "")) for c in cols) + " |"
                            for row in shown
                        ]
                        suffix = f"\n*({len(filtered_rows)} total rows)*" if capped else ""
                        _df_id_str = f"DF{_df_id}: " if _df_id else ""
                        table_sections.append(
                            f"**{_df_id_str}{df_name}** ({len(filtered_rows)} rows{filter_desc}):\n"
                            + header + "\n" + sep + "\n"
                            + "\n".join(body_lines)
                            + suffix
                        )
                        # Build an injected dataframe for the UI to render.
                        # If we applied a filter, use a descriptive label;
                        # otherwise reuse the original name.
                        if filter_desc:
                            _inj_label = f"{df_name}{filter_desc}"
                        else:
                            _inj_label = df_name
                        _injected_dfs[_inj_label] = {
                            "columns": cols,
                            "data": filtered_rows,
                            "row_count": len(filtered_rows),
                            "metadata": {
                                # Copy source metadata but drop df_id so the
                                # injected (filtered) df gets a fresh sequential ID.
                                **{k: v for k, v in _df_meta.items() if k != "df_id"},
                                "visible": True,
                                "source_df_id": _df_id,  # for progress notification
                            },
                        }
                        _found_in_block = True

                    # For explicit DF ref: keep scanning older blocks (the
                    # target DF may be anywhere in history).
                    # For heuristic (no explicit ref): stop at the first
                    # block that had matching data — it's the most recent.
                    if _found_in_block and _target_df_id is None:
                        break  # heuristic: most recent match is enough
                    if _found_in_block and _target_df_id is not None:
                        break  # explicit ref found — no need to keep scanning

                if table_sections:
                        logger.info(
                            "_inject_job_context: injecting data",
                            table_count=len(table_sections),
                            injected_df_count=len(_injected_dfs),
                            target_df_id=_target_df_id,
                        )
                        context_line = (
                            "[CONTEXT: This is a follow-up question. The answer "
                            "is likely in your previous query data below. "
                            "READ THIS DATA FIRST and answer directly from it. "
                            "Do NOT make a new DATA_CALL — the data is already provided. "
                            "Only make a new DATA_CALL if the data below is completely empty.]\n"
                            "[PREVIOUS QUERY DATA:]\n"
                            + "\n\n".join(table_sections)
                        )
                        _inject_debug = {
                            "source": "encode_df_injection",
                            "target_df_id": _target_df_id,
                            "file_type_filter": _file_type_filter,
                            "assay_filter": _assay_filter,
                            "output_type_filter": _output_type_filter,
                            "injected_df_names": list(_injected_dfs.keys()),
                            "injected_row_counts": {
                                k: v.get("row_count", len(v.get("data", [])))
                                for k, v in _injected_dfs.items()
                            },
                            "table_sections_count": len(table_sections),
                            "augmented_message_preview": context_line[:500],
                        }
                        return f"{context_line}\n\n{user_message}", _injected_dfs, _inject_debug
                else:
                    logger.info(
                        "_inject_job_context: no matching data found in history blocks",
                        target_df_id=_target_df_id,
                        file_type_filter=_file_type_filter,
                    )

            # FALLBACK: extract from <details> text in conversation history
            for hist_msg in reversed(conversation_history[-6:]):
                content = hist_msg.get("content", "")
                if hist_msg.get("role") != "assistant":
                    continue
                details_match = re.search(
                    r'<details>.*?<summary>.*?</summary>\s*(.*?)\s*</details>',
                    content, re.DOTALL
                )
                if details_match:
                    raw_data = details_match.group(1).strip()
                    if len(raw_data) > 6000:
                        raw_data = raw_data[:6000] + "\n... (truncated)"
                    context_line = (
                        "[CONTEXT: This is a follow-up question. The answer "
                        "may be in your previous query data below. Check this "
                        "data FIRST and answer directly if it contains the "
                        "answer. Only make a new DATA_CALL if this data does "
                        "NOT have what the user needs.]\n"
                        "[PREVIOUS QUERY DATA:]\n"
                        f"{raw_data}"
                    )
                    return f"{context_line}\n{user_message}", {}, {"source": "fallback_details"}

    return user_message, {}, {}


# ---------------------------------------------------------------------------
# Memory context injection
# ---------------------------------------------------------------------------

def inject_memory_context(
    user_message: str,
    memory_context: str,
) -> str:
    """Append [MEMORY: ...] block to the augmented message.

    Called from app.py after _inject_job_context, using the formatted string
    from memory_service.get_memory_context().
    """
    if not memory_context:
        return user_message
    return f"{user_message}\n\n{memory_context}"
