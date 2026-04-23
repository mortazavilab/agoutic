"""Mutable request-scoped context that flows through the chat pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatContext:
    """Shared state passed through every chat pipeline stage.

    Stages mutate fields in-place.  Setting ``response`` short-circuits the
    pipeline — the runner returns it immediately without calling later stages.
    """

    # ── Request ────────────────────────────────────────────────────────
    project_id: str = ""
    message: str = ""
    skill: str = "welcome"
    model: str = "default"
    request_id: str = ""
    user: Any = None          # cortex.models.User (set by endpoint)
    user_msg_lower: str = ""  # lowered copy, computed once

    # ── DB / blocks ────────────────────────────────────────────────────
    session: Any = None       # SQLAlchemy Session
    user_block: Any = None    # ProjectBlock for the USER_MESSAGE

    # ── Skill resolution ───────────────────────────────────────────────
    active_skill: str = ""
    pre_llm_skill: str = ""   # snapshot before auto-switch
    auto_skill: str | None = None

    # ── Conversation history ───────────────────────────────────────────
    history_blocks: list = field(default_factory=list)
    conversation_history: list = field(default_factory=list)  # OpenAI-format dicts
    conv_state: Any = None    # cortex.schemas.ConversationState

    # ── LLM engine ─────────────────────────────────────────────────────
    engine: Any = None        # cortex.agent_engine.AgentEngine
    augmented_message: str = ""
    raw_response: str = ""
    clean_markdown: str = ""

    # ── Tag-parse results ──────────────────────────────────────────────
    needs_approval: bool = False
    plot_specs: list = field(default_factory=list)
    data_call_matches: list = field(default_factory=list)
    legacy_encode_matches: list = field(default_factory=list)
    legacy_analysis_matches: list = field(default_factory=list)
    has_any_tags: bool = False

    # ── Tool execution ─────────────────────────────────────────────────
    auto_calls: list = field(default_factory=list)
    all_results: dict = field(default_factory=dict)
    provenance: list = field(default_factory=list)

    # ── DataFrames / images ────────────────────────────────────────────
    injected_dfs: dict = field(default_factory=dict)
    injected_previous_data: bool = False
    injected_was_capped: bool = False
    embedded_dataframes: dict = field(default_factory=dict)
    embedded_images: dict = field(default_factory=dict)
    pending_download_files: list = field(default_factory=list)
    pending_action_payloads: list = field(default_factory=list)
    pending_action_source_block: Any = None

    # ── Token usage ────────────────────────────────────────────────────
    think_usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    })
    analyze_usage: dict = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    })

    # ── Debug / tracing ────────────────────────────────────────────────
    inject_debug: dict = field(default_factory=dict)
    project_dir: str = ""
    project_dir_path: Any = None  # pathlib.Path | None
    output_violations: list = field(default_factory=list)
    active_chain: Any = None  # plan_chains.ChainDef | None

    # ── Override flags ─────────────────────────────────────────────────
    is_user_data_override: bool = False
    is_browsing_override: bool = False
    is_remote_browsing_override: bool = False
    is_sync_override: bool = False

    # ── Remote stage ───────────────────────────────────────────────────
    remote_stage_approval_context: dict | None = None
    sync_run_uuid: str = ""
    skip_llm_first_pass: bool = False
    skip_tag_parsing: bool = False
    skip_second_pass: bool = False

    # ── Pipeline control ───────────────────────────────────────────────
    response: dict | None = None  # set to short-circuit the pipeline

    def short_circuit(self, response: dict) -> None:
        """Set the response so the pipeline runner returns immediately."""
        self.response = response
