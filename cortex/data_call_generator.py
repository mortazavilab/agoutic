"""
Auto-generate DATA_CALL dicts — extracted from cortex/app.py (Tier 2).

Safety net: when the LLM fails to emit DATA_CALL tags, this function
detects obvious patterns in the user's message and auto-generates the
appropriate tool calls for ENCODE, Dogme, and browsing commands.
"""

import os
import re

from cortex.conversation_state import _extract_job_context_from_history
from cortex.encode_helpers import (
    _ENCODE_ASSAY_ALIASES,
    _extract_encode_search_term,
    _find_experiment_for_file,
)
from cortex.path_helpers import (
    _pick_file_tool,
    _resolve_file_path,
    _resolve_workflow_path,
)
from common.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# _auto_generate_data_calls
# ---------------------------------------------------------------------------

def _auto_generate_data_calls(user_message: str, skill_key: str,
                              conversation_history: list | None = None,
                              history_blocks: list | None = None,
                              project_dir: str = "") -> list[dict]:
    """
    Safety net: if the LLM failed to generate DATA_CALL tags, detect obvious
    patterns in the user's message and auto-generate the appropriate tool calls.

    Also resolves conversational references ("them", "each of them", "these")
    by scanning recent conversation history for accessions or biosample terms.

    Returns a list of dicts: [{"source_type": str, "source_key": str, "tool": str, "params": dict}]
    """
    calls = []
    msg_lower = user_message.lower()

    # --- DF reference / visualization follow-up: never auto-call ---
    # If the user references an existing DataFrame (DF1, DF2, ...) this is
    # a follow-up on in-memory data — no new API call is needed.
    if re.search(r'\bDF\s*\d+\b', user_message, re.IGNORECASE):
        return calls
    # Pure visualization intent (no new data request)
    _VIZ_KEYWORDS = ("plot", "chart", "graph", "histogram", "scatter",
                     "visualize", "visualise", "heatmap", "pie chart",
                     "bar chart", "box plot", "distribution")
    if any(kw in msg_lower for kw in _VIZ_KEYWORDS):
        return calls

    # --- Browsing commands (highest priority, skill-independent) ---
    # "list workflows", "list files" etc. always route to analyzer when there
    # is a project directory, regardless of the active skill.  Must run before
    # any skill-specific logic (e.g. ENCODE catch-all) to avoid mis-routing.
    job_context = _extract_job_context_from_history(
        conversation_history, history_blocks=history_blocks)
    work_dir = job_context.get("work_dir", "")
    run_uuid = job_context.get("run_uuid", "")
    workflows = job_context.get("workflows", [])
    # Derive the project directory — prefer parent-of-work_dir (when
    # work_dir is a workflow directory like .../test16/workflow1); fall back
    # to the explicitly-passed project_dir (from the chat endpoint).
    _project_dir = ""
    if work_dir:
        if re.search(r'/workflow\d+/?$', work_dir):
            _project_dir = work_dir.rstrip("/").rsplit("/", 1)[0]
        else:
            _project_dir = work_dir  # already project-level
    if not _project_dir and project_dir:
        _project_dir = project_dir

    # --- "list workflows" command ---
    if re.search(r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b', msg_lower):
        if _project_dir:
            calls.append({
                "source_type": "service", "source_key": "analyzer",
                "tool": "list_job_files",
                "params": {"work_dir": _project_dir, "max_depth": 1, "name_pattern": "workflow*"},
            })
        return calls

    # --- "list project files [in <path>]" — explicitly target project root ---
    _list_proj_m = re.search(
        r'\b(?:list|show)\s+project\s+files?\b'
        r'(?:\s+(?:in|under|at|of)\s+(.+?))?\s*$',
        user_message, re.IGNORECASE,
    )
    if _list_proj_m and _project_dir:
        _subpath = (_list_proj_m.group(1) or "").strip().strip('"\'')
        _target_wd = _resolve_workflow_path(
            _subpath, _project_dir, workflows,
        ) if _subpath else _project_dir
        _params: dict = {"work_dir": _target_wd, "max_depth": 1}
        calls.append({
            "source_type": "service", "source_key": "analyzer",
            "tool": "list_job_files",
            "params": _params,
        })
        return calls

    # --- "list files" / "list files in <path>" command ---
    _list_files_m = re.search(
        r'\b(?:list|show|what)\s+(?:the\s+)?files?\b'
        r'(?:\s+(?:in|under|at|of)\s+(.+?))?\s*$',
        user_message, re.IGNORECASE,
    )
    if _list_files_m:
        _subpath = (_list_files_m.group(1) or "").strip().strip('"\'')
        if _subpath:
            # Try work_dir/<subpath> first; fall back to project_dir/<subpath>
            _target_wd = ""
            _wd_candidate = _resolve_workflow_path(
                _subpath, work_dir, workflows,
            ) if work_dir else ""
            if _wd_candidate and os.path.isdir(_wd_candidate):
                _target_wd = _wd_candidate
            elif _project_dir and _project_dir != work_dir:
                _proj_candidate = _resolve_workflow_path(
                    _subpath, _project_dir, workflows,
                )
                if _proj_candidate and os.path.isdir(_proj_candidate):
                    _target_wd = _proj_candidate
            # Last resort: use whichever candidate we have, let the server
            # report "not found" if neither exists.
            if not _target_wd:
                _target_wd = _wd_candidate or (
                    f"{_project_dir.rstrip('/')}/{_subpath}" if _project_dir else ""
                )
        else:
            # No subpath → list current workflow dir
            _target_wd = work_dir or _project_dir or ""

        if _target_wd:
            _params_f: dict = {"work_dir": _target_wd}
            if _subpath:
                _params_f["max_depth"] = 1
            calls.append({
                "source_type": "service", "source_key": "analyzer",
                "tool": "list_job_files",
                "params": _params_f,
            })
        # If no resolvable path, return empty calls — the browsing error
        # handler in the results pipeline will show helpful suggestions.
        return calls

    # Organism lookup for KNOWN biosamples.  This is NOT exhaustive — it
    # exists only so we can add an organism= hint when we recognise the term.
    # Unknown terms still get sent to the API (see catch-all at the bottom).
    _KNOWN_ORGANISMS: dict[str, str] = {
        # Human
        "k562": "Homo sapiens", "gm12878": "Homo sapiens",
        "hela": "Homo sapiens", "hepg2": "Homo sapiens",
        "hek293": "Homo sapiens", "a549": "Homo sapiens",
        "mcf-7": "Homo sapiens", "mcf7": "Homo sapiens",
        "jurkat": "Homo sapiens", "imr-90": "Homo sapiens",
        "imr90": "Homo sapiens", "u2os": "Homo sapiens",
        "hff": "Homo sapiens", "wtc-11": "Homo sapiens",
        "lncap": "Homo sapiens", "panc-1": "Homo sapiens",
        "sk-n-sh": "Homo sapiens", "h1": "Homo sapiens",
        "h9": "Homo sapiens", "caco-2": "Homo sapiens",
        "sh-sy5y": "Homo sapiens",
        # Mouse
        "c2c12": "Mus musculus", "nih3t3": "Mus musculus",
        "mef": "Mus musculus", "mel": "Mus musculus",
        "es-e14": "Mus musculus", "mesc": "Mus musculus",
        "g1e": "Mus musculus", "ch12": "Mus musculus",
        "v6.5": "Mus musculus",
    }

    # --- ENCODE patterns ---
    # Detect ENCODE accession numbers (ENCSR, ENCFF, ENCLB, etc.)
    accession_matches = re.findall(r'(ENC[A-Z]{2}\d{3}[A-Z]{3})', user_message, re.IGNORECASE)
    accessions = [a.upper() for a in accession_matches]

    # --- Download intent detection ---
    # "download ENCFF921XAH" should resolve the file URL and start a download,
    # not trigger a get_files_by_type search.
    _download_intent = any(w in msg_lower for w in (
        "download", "grab", "fetch", "save", "get me",
    ))
    _encff_accessions = [a for a in accessions if a.startswith("ENCFF")]

    if _download_intent and _encff_accessions:
        # Find the parent experiment — check current message first, then history
        _encsr_in_msg = [a for a in accessions if a.startswith("ENCSR")]
        for _encff in _encff_accessions:
            _parent_exp = _encsr_in_msg[0] if _encsr_in_msg else \
                _find_experiment_for_file(_encff, conversation_history)
            if _parent_exp:
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_file_metadata",
                    "params": {"accession": _parent_exp, "file_accession": _encff},
                    "_chain": "download",  # signal to auto-download after metadata resolves
                })
            else:
                # No parent experiment — try to get metadata with just the ENCFF
                # (the _correct_tool_routing code will handle this)
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_file_metadata",
                    "params": {"file_accession": _encff},
                    "_chain": "download",
                })
        return calls

    # If no accession in the message, check for conversational references
    # ("them", "these", "each", "all of them", "for those", etc.)
    referential_words = ["them", "these", "those", "each", "all of them",
                         "each of them", "for those", "the experiments",
                         "the accessions", "same", "list"]
    _has_referential = any(w in msg_lower for w in referential_words)

    if not accessions and _has_referential:
        # Scan recent conversation history (last 4 messages) for accessions.
        # IMPORTANT: Strip <details>...</details> blocks first so we only
        # pick up accessions from the clean summary, not from raw query dumps
        # which may contain unrelated experiments from broader searches.
        if conversation_history:
            recent = conversation_history[-4:]
            for msg in recent:
                content = msg.get("content", "")
                # Remove raw data sections that may contain extra accessions
                content = re.sub(r'<details>.*?</details>', '', content, flags=re.DOTALL)
                found = re.findall(r'(ENC[A-Z]{2}\d{3}[A-Z]{3})', content, re.IGNORECASE)
                for acc in found:
                    acc_upper = acc.upper()
                    # Only use experiment-level accessions (ENCSR), not file accessions
                    if acc_upper.startswith("ENCSR") and acc_upper not in accessions:
                        accessions.append(acc_upper)

    if accessions and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
        # Determine which tool based on what the user is asking
        file_keywords = ["bam", "fastq", "file", "files", "pod5", "tar", "bigwig",
                         "download", "available", "accessions", "alignments"]
        # Note: "methylated" removed from file_keywords — it's a follow-up filter
        # handled by _inject_job_context, not a new fetch trigger.
        summary_keywords = ["summary", "how many files", "file size"]
        metadata_keywords = ["detail", "metadata", "info", "what is", "tell me about",
                            "describe", "experiment"]

        for accession in accessions:
            if any(kw in msg_lower for kw in file_keywords):
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_files_by_type", "params": {"accession": accession},
                })
            elif any(kw in msg_lower for kw in summary_keywords):
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_files_by_type", "params": {"accession": accession},
                })
            elif any(kw in msg_lower for kw in metadata_keywords):
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_experiment", "params": {"accession": accession},
                })
            else:
                # Default: get experiment details for an accession
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "get_experiment", "params": {"accession": accession},
                })

    # Detect biosample searches (no accession but mentions cell lines/tissues)
    elif skill_key in ("ENCODE_Search", "ENCODE_LongRead") and not accessions:
        # Detect assay-type filter in user message using the module-level map.
        detected_assay: str | None = None
        for alias, canonical in _ENCODE_ASSAY_ALIASES.items():
            if alias in msg_lower:
                detected_assay = canonical
                break

        # --- Strategy: try specific detections first, then catch-all ---
        # 1. Known biosample (organism hint available)
        # 2. Referential follow-up from conversation history
        # 3. Assay-only query
        # 4. Known target protein / histone mark
        # 5. CATCH-ALL: extract unknown term and send to search_by_biosample

        # 1. Check for a known biosample (lets us add organism= hint)
        for keyword, organism in _KNOWN_ORGANISMS.items():
            if keyword in msg_lower:
                # Grab the original-case version from the user message
                _orig_m = re.search(rf'\b({re.escape(keyword)})\b', user_message, re.IGNORECASE)
                _search_term = _orig_m.group(1) if _orig_m else keyword.upper()
                params: dict[str, str] = {"search_term": _search_term}
                # Only add organism if user explicitly mentioned species
                if "mouse" in msg_lower or "mus musculus" in msg_lower:
                    params["organism"] = "Mus musculus"
                elif "human" in msg_lower or "homo sapiens" in msg_lower:
                    params["organism"] = "Homo sapiens"
                if detected_assay:
                    params["assay_title"] = detected_assay
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "search_by_biosample", "params": params,
                })
                break

        # 2. Referential follow-up — scan history for previous biosample term
        if not calls and (_has_referential or detected_assay) and conversation_history:
            for hist_msg in reversed(conversation_history[-6:]):
                content_lower = hist_msg.get("content", "").lower()
                for keyword, organism in _KNOWN_ORGANISMS.items():
                    if keyword in content_lower:
                        _orig_m = re.search(rf'\b({re.escape(keyword)})\b',
                                            hist_msg.get("content", ""), re.IGNORECASE)
                        _search_term = _orig_m.group(1) if _orig_m else keyword.upper()
                        params = {"search_term": _search_term}
                        if detected_assay:
                            params["assay_title"] = detected_assay
                        calls.append({
                            "source_type": "consortium", "source_key": "encode",
                            "tool": "search_by_biosample", "params": params,
                        })
                        break
                if calls:
                    break

        # Assay-only query: assay detected but no biosample found in message or history.
        # e.g. "how many RNA-seq experiments are in ENCODE?"
        if not calls and detected_assay and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
            calls.append({
                "source_type": "consortium", "source_key": "encode",
                "tool": "search_by_assay",
                "params": {"assay_title": detected_assay},
            })

        # 4. Target-based query: no biosample, no assay, but a known target protein
        # e.g. "search ENCODE for CTCF" or "H3K27ac experiments"
        if not calls and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
            _known_targets = {
                "ctcf", "polr2a", "ep300", "max", "myc", "jun", "fos",
                "rest", "yy1", "tcf7l2", "gata1", "gata2", "spi1",
                "cebpb", "stat1", "stat3", "irf1", "nrf1", "rad21",
                "smc3", "nipbl", "znf143", "brd4", "mediator",
                "h3k27ac", "h3k4me3", "h3k4me1", "h3k36me3",
                "h3k27me3", "h3k9me3", "h3k79me2", "h2afz", "h4k20me1",
            }
            # Check if user mentions a known target (word-boundary match)
            for _tgt in _known_targets:
                if re.search(rf'\b{re.escape(_tgt)}\b', msg_lower):
                    # Use original case from user message for the target value
                    _tgt_match = re.search(rf'\b({re.escape(_tgt)})\b', user_message, re.IGNORECASE)
                    _tgt_val = _tgt_match.group(1) if _tgt_match else _tgt.upper()
                    calls.append({
                        "source_type": "consortium", "source_key": "encode",
                        "tool": "search_by_target",
                        "params": {"target": _tgt_val},
                    })
                    break

        # 5. CATCH-ALL: nothing matched above but we're on an ENCODE skill.
        #    Extract the most likely search term from the user's message and
        #    send it to search_by_biosample.  Let the ENCODE API decide if
        #    the term is valid — we can't enumerate every cell line / tissue.
        if not calls and skill_key in ("ENCODE_Search", "ENCODE_LongRead"):
            _extracted = _extract_encode_search_term(user_message)
            if _extracted:
                params = {"search_term": _extracted}
                # Infer organism from context words
                if "mouse" in msg_lower or "mus musculus" in msg_lower:
                    params["organism"] = "Mus musculus"
                elif "human" in msg_lower or "homo sapiens" in msg_lower:
                    params["organism"] = "Homo sapiens"
                if detected_assay:
                    params["assay_title"] = detected_assay
                calls.append({
                    "source_type": "consortium", "source_key": "encode",
                    "tool": "search_by_biosample", "params": params,
                })
                logger.info("Catch-all: extracted unknown search term for ENCODE",
                           search_term=_extracted)

    # --- Dogme / Analyzer file-parsing patterns ---
    # When in a Dogme analysis skill and user asks to parse/show a file,
    # auto-generate find_file + parse/read calls so the LLM gets real data.
    dogme_skills = {"run_dogme_dna", "run_dogme_rna", "run_dogme_cdna",
                    "analyze_job_results"}
    if not calls and skill_key in dogme_skills:
        # job_context, work_dir, run_uuid, workflows already extracted above
        # (browsing block at the top of this function).

        # --- "set workflow" / "use workflow" command ---
        # Handled conversationally — the LLM picks up which workflow to use.
        # We just inject context (done by _inject_job_context).

        # --- File-parse / read patterns ---
        parse_keywords = ["parse", "show me", "read", "open", "display",
                          "view", "get", "what's in", "contents of"]
        if any(kw in msg_lower for kw in parse_keywords):
            # Extract filename / relative path from user message.
            # Handles: "parse annot/File.csv", "parse workflow2/annot/File.csv",
            #          "parse File.csv", "show me the file File.csv"
            file_pattern = (
                r'(?:parse|show\s+me|read|open|display|view|get)'
                r'\s+(?:the\s+)?(?:file\s+)?'
                r'(\S+\.(?:csv|tsv|bed|txt|log|html))'
            )
            file_match = re.search(file_pattern, msg_lower)
            if file_match:
                filename = file_match.group(1)
                # Grab the original-case version from the raw message
                file_match_orig = re.search(file_pattern, user_message, re.IGNORECASE)
                if file_match_orig:
                    filename = file_match_orig.group(1)

                # Resolve the path: could be just a filename, a subpath
                # (annot/File.csv), or workflow-prefixed (workflow2/annot/File.csv).
                _resolved_wd, _resolved_file = _resolve_file_path(
                    filename, work_dir, workflows,
                )
                if not _resolved_wd and work_dir:
                    _resolved_wd = work_dir
                if not _resolved_wd and run_uuid:
                    _resolved_wd = None  # will use run_uuid fallback

                if _resolved_wd or run_uuid:
                    _params: dict = {"file_name": _resolved_file}
                    if _resolved_wd:
                        _params["work_dir"] = _resolved_wd
                    else:
                        _params["run_uuid"] = run_uuid
                    calls.append({
                        "source_type": "service", "source_key": "analyzer",
                        "tool": "find_file",
                        "params": _params,
                        "_chain": _pick_file_tool(_resolved_file),
                    })

        # --- Catch-all for analyze_job_results: if the LLM narrated steps
        # instead of emitting a DATA_CALL, auto-generate get_analysis_summary
        # so the analysis actually executes. ---
        if not calls and skill_key == "analyze_job_results" and (work_dir or run_uuid):
            _summary_params: dict = {}
            if work_dir:
                _summary_params["work_dir"] = work_dir
            if run_uuid:
                _summary_params["run_uuid"] = run_uuid
            calls.append({
                "source_type": "service", "source_key": "analyzer",
                "tool": "get_analysis_summary",
                "params": _summary_params,
            })
            logger.warning("Auto-generated get_analysis_summary for analyze_job_results skill",
                          work_dir=work_dir, run_uuid=run_uuid)

    return calls


# ---------------------------------------------------------------------------
# _validate_analyzer_params
# ---------------------------------------------------------------------------

def _validate_analyzer_params(
    tool: str, params: dict, user_message: str,
    conversation_history: list | None = None,
    history_blocks: list | None = None,
    project_dir: str = "",
) -> dict:
    """
    Always force *work_dir* from conversation context and strip unknown params.

    The LLM cannot be trusted to produce the correct work_dir — it may
    emit a placeholder (``/work_dir``, ``{work_dir}``), an invented path,
    or the project-level dir instead of a workflow dir.  We therefore
    always resolve the real work_dir from history and override whatever
    the LLM supplied.

    Also strips parameters that the Analyzer MCP tool doesn't accept
    (e.g. ``sample=Jamshid``) to prevent Pydantic validation errors.
    """
    params = dict(params)  # shallow copy

    # --- Strip unknown params for each tool ---
    _KNOWN_PARAMS: dict[str, set[str]] = {
        "list_job_files": {"work_dir", "run_uuid", "extensions", "compact", "max_depth"},
        "find_file": {"file_name", "work_dir", "run_uuid"},
        "read_file_content": {"file_path", "work_dir", "run_uuid", "preview_lines"},
        "parse_csv_file": {"file_path", "work_dir", "run_uuid", "max_rows"},
        "parse_bed_file": {"file_path", "work_dir", "run_uuid", "max_records"},
        "get_analysis_summary": {"run_uuid", "work_dir"},
        "categorize_job_files": {"work_dir", "run_uuid"},
    }
    allowed = _KNOWN_PARAMS.get(tool)

    # --- Rescue subfolder hints before stripping unknown params ---
    # The LLM may pass invented params like subfolder=annot, path=annot,
    # directory=annot etc.  Capture these before they're stripped.
    _subfolder_hint = ""
    for _sf_key in ("subfolder", "subpath", "path", "directory", "folder", "subdir"):
        if _sf_key in params and (not allowed or _sf_key not in allowed):
            _subfolder_hint = params[_sf_key]
            break

    if allowed:
        _extra = set(params) - allowed
        if _extra:
            logger.warning("Stripping unknown Analyzer params",
                          tool=tool, extra_params=sorted(_extra))
            params = {k: v for k, v in params.items() if k in allowed}

    # --- Force work_dir from context ---
    ctx = _extract_job_context_from_history(
        conversation_history, history_blocks=history_blocks
    )
    real_wd = ctx.get("work_dir", "")

    # If multiple workflows, pick the matching one by filename/sample
    if not real_wd and ctx.get("workflows"):
        real_wd = ctx["workflows"][-1].get("work_dir", "")
    # Fallback: use the project directory if no workflow-level work_dir
    if not real_wd and project_dir:
        real_wd = project_dir
    workflows = ctx.get("workflows", [])
    if len(workflows) > 1:
        _fname = params.get("file_name", "").lower()
        for wf in workflows:
            sn = wf.get("sample_name", "").lower()
            if sn and (_fname and sn in _fname):
                real_wd = wf["work_dir"]
                break

    llm_wd = params.get("work_dir", "")
    if real_wd:
        # "list workflows" needs the *project* dir, not a workflow dir.
        if tool == "list_job_files" and re.search(
            r'\b(?:list|show|what)\s+(?:the\s+)?(?:available\s+)?workflows?\b',
            user_message, re.IGNORECASE,
        ):
            # If real_wd came from a workflow (has /workflow\d+/ suffix),
            # strip to the parent project directory.
            if re.search(r'/workflow\d+/?$', real_wd):
                real_wd = real_wd.rstrip("/").rsplit("/", 1)[0]
            # Otherwise real_wd is already the project dir (from project_dir fallback)
            params["max_depth"] = 1  # only top-level dirs
            params["name_pattern"] = "workflow*"  # filter to workflow dirs only

        # "list files in <subpath>" — append subfolder to work_dir.
        # Source 1 (preferred): parse from user message — preserves
        #   workflow prefix (e.g. "workflow1/annot") that the LLM may strip.
        # Source 2 (fallback): subfolder hint from LLM's invented param.
        if tool == "list_job_files" and not params.get("max_depth"):
            _sub = ""
            _sub_m = re.search(
                r'\b(?:list|show)\s+(?:the\s+)?files?\s+'
                r'(?:in|under|at|of)\s+(.+)',
                user_message, re.IGNORECASE,
            )
            if _sub_m:
                _sub = _sub_m.group(1).strip().strip('"\'')
            if not _sub:
                _sub = _subfolder_hint
            if _sub:
                real_wd = _resolve_workflow_path(_sub, real_wd, workflows)
                # Show only immediate contents of the subfolder, not deep recursion
                params["max_depth"] = 1

        # If the incoming work_dir is already a valid subdirectory of the
        # resolved context dir OR the project dir, keep it — auto-generated
        # calls (from _auto_generate_data_calls) already resolved the correct
        # path and overriding would lose the subfolder.
        # Derive project dir from real_wd (strip /workflowN suffix).
        _proj_wd = (
            real_wd.rstrip("/").rsplit("/", 1)[0]
            if re.search(r'/workflow\d+/?$', real_wd)
            else real_wd
        )
        _is_valid_sub = llm_wd and (
            llm_wd.startswith(real_wd.rstrip("/") + "/")
            or llm_wd.startswith(_proj_wd.rstrip("/") + "/")
            or llm_wd == _proj_wd  # exact match on project dir
        )
        if _is_valid_sub:
            logger.info(
                "Keeping incoming work_dir (valid subdirectory of context/project)",
                incoming=llm_wd, context_wd=real_wd, project_wd=_proj_wd, tool=tool,
            )
            params["work_dir"] = llm_wd
        else:
            if real_wd != llm_wd:
                logger.warning(
                    "Overriding LLM work_dir with context value",
                    llm_value=llm_wd, resolved=real_wd, tool=tool,
                )
            params["work_dir"] = real_wd
    elif not llm_wd:
        logger.warning(
            "No work_dir in context or LLM params", tool=tool,
        )

    return params
