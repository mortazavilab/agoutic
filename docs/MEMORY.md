# Memory System Architecture

> **Current as of v3.6.6**

The memory system gives AGOUTIC a long-lived, cross-session store for results,
sample annotations, pipeline steps, preferences, findings, and dataframes.
Memories surface automatically in every LLM turn via an injected `[MEMORY]`
block and can be created, searched, and managed through slash commands, natural
language, or the Memories UI page.

## Table of Contents

1. [Data Model](#data-model)
2. [Categories & Sources](#categories--sources)
3. [Scopes](#scopes)
4. [Slash Commands](#slash-commands)
5. [Natural Language Detection](#natural-language-detection)
6. [Auto-Capture](#auto-capture)
7. [Memory Context Injection](#memory-context-injection)
8. [Dataframe Memories](#dataframe-memories)
9. [DF Context Injection](#df-context-injection)
10. [REST API](#rest-api)
11. [UI](#ui)
12. [Database](#database)
13. [Key Modules](#key-modules)

---

## Data Model

The `memories` table is defined in `cortex/models.py` as the `Memory`
SQLAlchemy model.

| Column | Type | Description |
|--------|------|-------------|
| `id` | `String` PK | UUID-v4 |
| `user_id` | `String` | Owner; indexed |
| `project_id` | `String \| NULL` | `NULL` = user-global; set = project-scoped; indexed |
| `category` | `String` | One of the [valid categories](#valid-categories) |
| `content` | `Text` | Human-readable summary line |
| `structured_data` | `Text \| NULL` | JSON payload (full DF rows, annotation dict, step metadata, etc.) |
| `source` | `String` | One of the [valid sources](#valid-sources) |
| `is_pinned` | `Boolean` | Pinned memories appear first in `[MEMORY]` context |
| `is_deleted` | `Boolean` | Soft-delete flag — records are never hard-deleted |
| `deleted_at` | `DateTime \| NULL` | Set on soft-delete; cleared on restore |
| `related_block_id` | `String \| NULL` | Originating conversation block |
| `related_file_id` | `String \| NULL` | Originating user file; indexed |
| `tags_json` | `Text \| NULL` | JSON dict (e.g. `{"df_id": 5, "df_name": "c2c12DF"}`) |
| `created_at` | `DateTime` | UTC, server default |
| `updated_at` | `DateTime` | UTC, server default |

**Indexes**: `(user_id)`, `(project_id)`, `(related_file_id)`, and composite
`(user_id, project_id, is_deleted)` for the main query hot-path.

---

## Categories & Sources

### Valid Categories

Stored in `cortex/memory_service.VALID_CATEGORIES`.

| Category | Created by | Description |
|----------|-----------|-------------|
| `result` | Auto / user | Completed job result summary |
| `sample_annotation` | User / NL | Sample condition or metadata annotation |
| `pipeline_step` | Auto | Completed workflow plan step |
| `preference` | User | User-stated preference or style note |
| `finding` | User | Analysis finding or conclusion |
| `custom` | User | Freeform note (default for `/remember`) |
| `dataframe` | User | Saved conversation dataframe |

### Valid Sources

Stored in `cortex/memory_service.VALID_SOURCES`.

| Source | Description |
|--------|-------------|
| `user_manual` | Created via slash command or UI |
| `auto_step` | Recorded automatically when a pipeline step completes |
| `auto_result` | Recorded automatically when a job completes |
| `system` | Written by the system at initialisation |

---

## Scopes

| Scope | `project_id` | Visible in |
|-------|-------------|------------|
| Project-scoped | UUID string | The originating project (and always included when listing with `include_global=True`) |
| User-global | `NULL` | All projects for this user |

`list_memories()` in `cortex/memory_service.py` returns both project-scoped and
global memories for the active project by default (`include_global=True`). Pass
`project_id=None` to get global-only memories.

### Upgrade to Global

Call `upgrade_to_global()` or use `/upgrade-to-global #<id>` to promote a
project-scoped memory to global scope. The only restriction:
**unnamed dataframe memories** may not be promoted — they must first be given an
alias via `/remember-df DFn as <name>`.

---

## Slash Commands

Parsed by `parse_memory_command()` in `cortex/memory_commands.py`. All slash
commands are handled by the backend without calling the LLM — zero token cost.

| Command | Syntax | Description |
|---------|--------|-------------|
| `/remember` | `/remember <text>` | Save a project-scoped memory |
| `/remember-global` | `/remember-global <text>` | Save a user-global memory |
| `/remember-df` | `/remember-df DF5` or `/remember-df DF5 as c2c12DF` | Save a conversation dataframe; optional user-given alias |
| `/forget` | `/forget <text>` or `/forget #<id>` | Soft-delete by content match or ID prefix |
| `/memories` | `/memories` or `/memories --global` | List active memories (project + global, or global only) |
| `/pin` | `/pin #<id>` | Pin a memory (shown first in `[MEMORY]` context) |
| `/unpin` | `/unpin #<id>` | Unpin a memory |
| `/restore` | `/restore #<id>` | Restore a soft-deleted memory |
| `/annotate` | `/annotate <sample> <key>=<value>...` | Annotate a sample with key/value metadata |
| `/search-memories` | `/search-memories <query>` | Full-text search across memory content |
| `/upgrade-to-global` | `/upgrade-to-global #<id>` | Promote a project memory to global scope |

**Aliases**: `/make-global` and `/upgrade-global` are equivalent to
`/upgrade-to-global`.

Memory IDs in list output are displayed as 8-character prefixes (e.g. `a1b2c3d4`).
Use them preceded by `#` in pin, forget, restore, and upgrade commands.

---

## Natural Language Detection

`detect_memory_intent()` in `cortex/memory_commands.py` scans the user message
_before_ it reaches the LLM. If a pattern matches, the side-effect fires in
parallel with the normal LLM response — the original message still goes to the
LLM unchanged.

Detection is skipped when the message starts with `/`.

| Pattern | Example | Action |
|---------|---------|--------|
| `remember [that] ...` | "remember that sample A is from mouse" | `remember` → project-scoped memory |
| `note that ...` / `save that ...` | "note that I prefer bar charts" | `remember` |
| `sample X is [a/an] Y` | "sample C2C12 is a myoblast cell line" | `annotate` → `condition=Y` |
| `mark/label/tag sample X as Y` | "mark sample KO as treated" | `annotate` → `condition=Y` |
| `forget the memory about ...` | "forget the memory about the old run" | `forget` by content |
| `what do you remember about ...?` | "what do you remember about sample B?" | `search` |

---

## Auto-Capture

Memories are created automatically without user intervention in two scenarios.

### Pipeline Steps (`source="auto_step"`)

When the plan executor marks a workflow step as completed, a `pipeline_step`
memory is created recording the step kind, sample name, and step ID.
Deduplication by content ensures repeated step runs do not create duplicates.

### Job Results (`source="auto_result"`, `is_pinned=True`)

When the polling loop marks a job as completed or failed, a `result` memory is
automatically created and **pinned**. It records the run UUID, sample name,
workflow type, and final status, and always appears at the top of the `[MEMORY]`
context block.

---

## Memory Context Injection

`get_memory_context()` in `cortex/memory_service.py` builds the `[MEMORY]`
block prepended to the system prompt before every LLM turn.

### Ordering

1. **Pinned** memories (pinned flag set), newest first
2. **Sample annotation** memories
3. All remaining active memories, newest first

### Token Budget

The block is rendered line by line; once the running token count reaches
**2,000 tokens** (configurable via `MEMORY_CONTEXT_BUDGET`), rendering stops
and a `… (N more)` footer is appended. Soft-deleted memories are never included.

### Format in the Prompt

```
[MEMORY]
⭐ [result] Run abc123ef — sample C2C12, workflow dogme, status COMPLETED
⭐ [sample_annotation] sample=C2C12 condition=myoblast
[preference] prefer grouped bar charts
[pipeline_step] LOCATE_DATA step completed for C2C12 (workflow3)
```

The system prompt instructs the LLM to reference `[MEMORY]` for sample
conditions, prior results, and stated preferences before generating code or
plans.

---

## Dataframe Memories

Any conversation dataframe can be saved as a named or unnamed memory with
`/remember-df DF5 as c2c12DF`.

### `remember_dataframe()`

Defined in `cortex/memory_service.py`. Stores:

| Field | Content |
|-------|---------|
| `content` | Human-readable line, e.g. `DF5 "c2c12DF" — ENCODE results (50 rows, 8 cols)` |
| `structured_data` | Full JSON: `columns`, `data`, `row_count`, `label` |
| `tags_json` | `{"df_id": 5, "df_name": "c2c12DF"}` (no `df_name` key for unnamed DFs) |
| `category` | `"dataframe"` |

### `get_remembered_df_map()`

Returns a `dict[int | str, dict]` for merging into the conversation's `df_map`:

| Key type | When used | Example |
|----------|-----------|---------|
| `str` | Named DF (has `df_name` in `tags_json`) | `"c2c12DF"` |
| `int` (≥ 900) | Unnamed DF | `900`, `901`, … |

Global memories are merged alongside project-scoped ones (up to 50 total).

A named remembered DF behaves exactly like a conversation DF:

- visible in `list dfs`
- accessible via `head c2c12DF` or `head c2c12DF 20`
- injected into the LLM prompt when referenced by name in a user message

### Upgrade to Global

Named DF memories can be promoted from project scope to global via
`/upgrade-to-global #<id>` or the 🌐 button on the Memories UI card.
Unnamed DFs are blocked — they must be named first.

---

## DF Context Injection

When a user message references a named remembered DF, `_inject_job_context()`
in `cortex/context_injection.py` injects the full table into the LLM prompt so
the model can plot or analyse the DF without making a new `DATA_CALL`.

### Trigger

A `\bname\b` regex (case-insensitive) is run against the user message for every
named key in the remembered DF map. The first match triggers injection.

### Call Signature

```python
_inject_job_context(
    user_message,
    active_skill,
    conversation_history,
    history_blocks=None,
    db=None,          # required for named DF lookup
    user_id=None,     # required
    project_id=None,
) -> tuple[str, dict, dict]
#          ^augmented_message, injected_dfs, debug_info
```

All three of `db`, `user_id`, and `project_id` must be provided; if any is
`None`, the named DF lookup path is skipped entirely.

### Three Injection Paths

| Skill type | Behaviour |
|-----------|-----------|
| Dogme skills | Appends `[NOTE: <name> is a remembered DataFrame…]` annotation to the user message |
| ENCODE skills | Treats the message as an ENCODE follow-up; injects full table as `[PREVIOUS QUERY DATA:]` |
| All other skills | Appends full table as `[PREVIOUS QUERY DATA:]` |

The table is capped at **500 rows**; a `(N total rows)` suffix is appended when
truncated. The injected DF dict is also returned so the UI debug panel can
render it.

---

## REST API

All endpoints are registered under `/memories` by `cortex/routes/memories.py`
and require an authenticated session cookie.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/memories` | List memories. Query params: `project_id`, `category`, `include_global`, `include_deleted`, `pinned_only`, `show_all`, `limit` (max 500) |
| `POST` | `/memories` | Create a memory. Body: `MemoryCreate` schema |
| `DELETE` | `/memories/{id}` | Soft-delete a memory |
| `PATCH` | `/memories/{id}` | Update content, tags, or pin status (`MemoryUpdate` schema) |
| `POST` | `/memories/{id}/restore` | Undo soft-delete |
| `POST` | `/memories/{id}/upgrade-to-global` | Promote project memory to global scope |
| `GET` | `/memories/search?q=<text>` | Full-text (LIKE) search across content |
| `GET` | `/memories/context?project_id=<id>` | Return the rendered `[MEMORY]` block (for debugging) |

---

## UI

### Memories Page (`ui/pages/memories.py`)

Accessible via the sidebar "View all memories →" link.

- **Scope display** — each card shows 📁 `<project-slug>` or 🌐 Global
- **Category filter** — chips for all 7 categories
- **Pinned-only toggle**
- **➕ Add Memory** — expandable form (content, category, scope, pin)
- **Per-card controls:**
  - 🗑️ Delete (soft-delete with confirmation)
  - ♻️ Restore (visible for deleted items when `include_deleted=True`)
  - 🌐 Upgrade to global (disabled with tooltip for unnamed DFs)
  - DF preview for `dataframe` category (column list, named/unnamed warning)

### Sidebar Widget (`ui/appui_sidebar.py`)

A collapsible `🧠 Memories (N)` expander shows up to 5 recent memories with
pinned/global badges and first 60 characters of content, plus a
`View all memories →` page link.

The `❓ Help` expander in the sidebar also includes quick-access buttons for
`/memories`, `list dfs`, and `try again`, with inline captions for the most
common memory and DF command syntax.

---

## Database

### Alembic Migration

| Property | Value |
|----------|-------|
| Migration ID | `a1b2c3d4e5f6` |
| Table created | `memories` |
| Composite index | `(user_id, project_id, is_deleted)` |
| Additional index | `(related_file_id)` |

Apply with:

```bash
alembic upgrade head
# or target this migration specifically:
alembic upgrade a1b2c3d4e5f6
```

---

## Key Modules

| File | Role |
|------|------|
| `cortex/models.py` | `Memory` SQLAlchemy model |
| `cortex/memory_service.py` | All CRUD, query, auto-capture, context building, and DF-map helpers |
| `cortex/memory_commands.py` | Slash command parser (`parse_memory_command`) and NL intent detector (`detect_memory_intent`) |
| `cortex/context_injection.py` | `_inject_job_context` — named DF injection into LLM turns |
| `cortex/routes/memories.py` | FastAPI router — 8 REST endpoints |
| `ui/pages/memories.py` | Full-page Memories browser and management UI |
| `ui/appui_sidebar.py` | Sidebar 🧠 Memories widget and ❓ Help command shortcuts |
| `tests/test_memory.py` | 73 tests covering the full memory surface |
