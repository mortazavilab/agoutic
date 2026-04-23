# Chat Pipeline Architecture

The `/chat` endpoint (`cortex/app.py → chat_with_agent`) uses a **registry-based
dispatch pipeline** that processes each user message through an ordered sequence
of stages.  Each stage is a small, self-contained class that inspects or mutates
a shared **ChatContext** object.  Any stage may *short-circuit* the pipeline by
setting `ctx.response`, which causes the runner to return immediately.

---

## High-Level Flow

```
request  ──▶  ChatContext  ──▶  run_chat_pipeline(ctx)
                                    │
               ┌────────────────────┘
               ▼
         ┌───────────┐
         │ Setup 100  │  session, skill, user block
         └─────┬─────┘
               ▼
         ┌───────────────────┐
         │ Quick Exits 200–240│  capabilities, prompt inspect,
         │                    │  df command, memory commands
         └─────┬──────────────┘
               ▼  (may short-circuit)
         ┌───────────┐
         │ History 300│  conversation history
         └─────┬─────┘
               ▼
         ┌──────────────────────┐
         │ Confirmation 350      │  resume pending dataframe actions
         └─────┬────────────────┘
               ▼
         ┌──────────────┐
         │ ContextPrep 400│  engine, project dir, context injection
         └─────┬────────┘
               ▼
         ┌─────────────────┐
         │ PlanDetection 500│  classify request, generate plan
         └─────┬───────────┘
               ▼  (may short-circuit for MULTI_STEP)
         ┌───────────────┐
         │ FirstPassLLM 600│  LLM think(), skill-switch re-think
         └─────┬─────────┘
               ▼
         ┌──────────────┐
         │ TagParsing 700│  approval, data_call, plot tags
         └─────┬────────┘
               ▼
         ┌──────────────┐
         │ Overrides 800 │  browsing, sync, auto-generate, hallucination guard
         └─────┬────────┘
               ▼
         ┌──────────────────┐
         │ ToolExecution 900 │  build, dedup, validate, execute MCP calls
         └─────┬────────────┘
               ▼
         ┌───────────────┐
         │ SecondPass 1000│  LLM analysis of tool results
         └─────┬─────────┘
               ▼
         ┌─────────────────────┐
         │ ResponseAssembly 1100│  blocks, DFs, plots, final response
         └─────────────────────┘
               │
               ▼
           ctx.response  ──▶  JSON to client
```

---

## Key Components

### ChatContext  (`cortex/chat_context.py`)

A mutable `@dataclass` that carries **all** pipeline state for a single
request.  Every stage reads and writes fields on this object; no globals are
used.

| Group | Fields (representative) |
|-------|------------------------|
| Request | `project_id`, `message`, `skill`, `model`, `request_id`, `user`, `user_msg_lower` |
| DB / Blocks | `session`, `user_block` |
| Skill | `active_skill`, `pre_llm_skill`, `auto_skill` |
| History | `history_blocks`, `conversation_history`, `conv_state` |
| LLM | `engine`, `augmented_message`, `raw_response`, `clean_markdown` |
| Tags | `needs_approval`, `plot_specs`, `data_call_matches`, `pending_action_payloads`, `has_any_tags` |
| Tools | `auto_calls`, `all_results`, `provenance` |
| DataFrames | `injected_dfs`, `embedded_dataframes`, `embedded_images`, `pending_download_files`, `pending_action_source_block` |
| Tokens | `think_usage`, `analyze_usage` |
| Overrides | `is_user_data_override`, `is_browsing_override`, `is_remote_browsing_override`, `is_sync_override` |
| Control | `skip_llm_first_pass`, `skip_tag_parsing`, `skip_second_pass`, **`response`** |

**`short_circuit(response)`** — helper that sets `ctx.response` and returns, so
a stage can call `ctx.short_circuit(resp)` to exit the pipeline.

### ChatStage Protocol  (`cortex/chat_stages/__init__.py`)

```python
class ChatStage(Protocol):
    name: str
    priority: int

    def should_run(self, ctx: ChatContext) -> bool: ...
    async def run(self, ctx: ChatContext) -> None: ...
```

- **`name`** — human-readable label (used in logs).
- **`priority`** — integer that controls execution order (lower = earlier).
- **`should_run(ctx)`** — return `False` to skip the stage for this request.
- **`run(ctx)`** — the stage body; mutates `ctx` or sets `ctx.response`.

### Stage Registry  (`cortex/chat_stages/__init__.py`)

```python
register_stage(stage: ChatStage) -> None   # auto-sorts by priority
get_stages() -> list[ChatStage]            # returns sorted list
```

Each stage module calls `register_stage(MyStage())` at **import time**.  The
registry is populated by glob-importing all stage modules in
`cortex/chat_stages/__init__.py`.

### Pipeline Runner  (`cortex/chat_pipeline.py`)

```python
async def run_chat_pipeline(ctx: ChatContext) -> dict:
    for stage in get_stages():
        if stage.should_run(ctx):
            await stage.run(ctx)
        if ctx.response is not None:
            return ctx.response
    raise RuntimeError("no stage produced a response")
```

---

## Stage Reference

### 100 — Setup  (`chat_stages/setup.py`)

Auto-registers the project if missing, checks the token usage limit, opens a
DB session, resolves the active skill from project history, and creates the
`USER_MESSAGE` block.

### 200 — Capabilities  (`chat_stages/quick_exits.py`)

Detects "what can you do" / "capabilities" / "help" messages and returns a
canned feature list.  **Short-circuits.**

### 210 — Prompt Inspection  (`chat_stages/quick_exits.py`)

Returns the rendered first-pass or second-pass system prompt on request
(e.g. "show first-pass system prompt").  **Short-circuits.**

### 220 — DataFrame Command  (`chat_stages/quick_exits.py`)

Handles `list dataframes` and `head DF5` commands without invoking the LLM.
**Short-circuits.**

### 230 — Memory Slash Commands  (`chat_stages/quick_exits.py`)

Executes `/memory` CRUD commands (`/remember`, `/forget`, `/memories`, etc.).
**Short-circuits.**

### 240 — Memory Intent  (`chat_stages/quick_exits.py`)

Detects natural-language memory phrases ("remember that …", "forget the
memory about …") and executes the corresponding memory operation.
**Short-circuits.**

### 300 — History  (`chat_stages/history.py`)

Loads `USER_MESSAGE`, `AGENT_PLAN`, and `EXECUTION_JOB` blocks, formats them
into OpenAI-style messages, and caps at `MAX_HISTORY_TURNS=20` user–assistant
pairs.

### 350 — Confirmation Detection  (`chat_stages/confirmation_detection.py`)

Detects short affirmative replies such as `yes` and resumes the most recent
pending dataframe action. It populates `ctx.auto_calls`, sets skip flags to
avoid a redundant LLM round-trip, and marks the saved pending-action block as
confirmed.

### 400 — Context Preparation  (`chat_stages/context_prep.py`)

Initialises the `AgentEngine`, resolves the project directory, detects
pre-LLM skill switches, and injects job, memory, and conversation-state
context into the engine.

### 500 — Plan Detection  (`chat_stages/plan_detection.py`)

Classifies the request type.  For `CHAIN_MULTI_STEP`, injects plan hints into
the message and continues.  For `MULTI_STEP`, generates a workflow plan and
returns it.  **Short-circuits for MULTI_STEP.**

### 600 — First-Pass LLM  (`chat_stages/llm_first_pass.py`)

Checks for cancellation, calls `engine.think()`, validates output, and handles
`[[SKILL_SWITCH_TO:…]]` by re-injecting context and re-calling the LLM with
the new skill.

### 700 — Tag Parsing  (`chat_stages/tag_parsing.py`)

Parses `[[APPROVAL:…]]`, `[[DATA_CALL:…]]`, `[[PLOT:…]]`, and legacy tags
from the LLM response.  Cleans markdown, fixes hallucinated accessions, and
applies plot fallbacks.

### 800 — Overrides  (`chat_stages/overrides.py`)

Detects user-data listing, browsing, remote-browsing, sync-results, and
auto-generate overrides.  Suppresses LLM tags and injects analyzer / launchpad
tool calls as needed.  Runs a hallucination guard on accession references.

### 900 — Tool Execution  (`chat_stages/tool_execution.py`)

Builds the combined tool-call list from parsed tags and auto-calls,
deduplicates and validates schemas, executes calls via MCP, records
provenance, and attempts chain-recovery for echoed `find_file` JSON.

### 1000 — Second-Pass LLM  (`chat_stages/second_pass.py`)

Runs only when tool calls produced results.  Formats results, determines
presentation mode (browsing / download / sync / approval / standard), and
calls `engine.analyze_results()` for a second LLM pass.

### 1100 — Response Assembly  (`chat_stages/response_assembly.py`)

Extracts embedded DataFrames and images, assigns DF IDs, rebinds and
deduplicates plot specs, creates `AGENT_PLAN` / `AGENT_PLOT` /
`APPROVAL_GATE` / `PENDING_ACTION` blocks, saves the conversation message,
and short-circuits with the final response dict.  **Always short-circuits**
(final stage).

---

## Adding a New Stage

1. Create a file under `cortex/chat_stages/` (e.g. `my_stage.py`).
2. Define a class implementing the `ChatStage` protocol.
3. Call `register_stage(MyStage())` at module level.
4. Choose a priority using the gap convention below.

```python
from cortex.chat_stages import register_stage
from cortex.chat_context import ChatContext

class MyStage:
    name = "my-stage"
    priority = 750          # runs between tag-parsing (700) and overrides (800)

    def should_run(self, ctx: ChatContext) -> bool:
        return some_condition(ctx)

    async def run(self, ctx: ChatContext) -> None:
        # mutate ctx ...
        pass

register_stage(MyStage())
```

### Priority Gap Convention

Priorities use gaps of **100** between major stages, leaving room for future
insertion without renumbering:

| Range | Purpose |
|-------|---------|
| 100 | Setup |
| 200–299 | Quick exits (no LLM) |
| 300 | History |
| 350 | Pending-action confirmation |
| 400 | Context preparation |
| 500 | Plan detection |
| 600 | First-pass LLM |
| 700 | Tag parsing |
| 800 | Overrides |
| 900 | Tool execution |
| 1000 | Second-pass LLM |
| 1100 | Response assembly |

---

## File Map

| File | Purpose |
|------|---------|
| `cortex/chat_context.py` | ChatContext dataclass |
| `cortex/chat_pipeline.py` | Pipeline runner |
| `cortex/chat_stages/__init__.py` | ChatStage protocol, registry, module imports |
| `cortex/chat_stages/setup.py` | Stage 100 |
| `cortex/chat_stages/quick_exits.py` | Stages 200–240 |
| `cortex/chat_stages/history.py` | Stage 300 |
| `cortex/chat_stages/confirmation_detection.py` | Stage 350 |
| `cortex/chat_stages/context_prep.py` | Stage 400 |
| `cortex/chat_stages/plan_detection.py` | Stage 500 |
| `cortex/chat_stages/llm_first_pass.py` | Stage 600 |
| `cortex/chat_stages/tag_parsing.py` | Stage 700 |
| `cortex/chat_stages/overrides.py` | Stage 800 |
| `cortex/chat_stages/tool_execution.py` | Stage 900 |
| `cortex/chat_stages/second_pass.py` | Stage 1000 |
| `cortex/chat_stages/response_assembly.py` | Stage 1100 |
