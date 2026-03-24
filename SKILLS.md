# Skills System

The Agoutic skills system lets you teach the agent new workflows by writing
Markdown files.  Each skill defines scope, routing rules, tool usage, and
optional multi-step plan chains — all of which the LLM reads at runtime to
decide how to handle a user request.

---

## How Skills Work

1. **Registry** — `cortex/config.py` maps a skill key to a relative
  `skills/<skill_key>/SKILL.md` path via `SKILLS_REGISTRY`:

   ```python
   SKILLS_REGISTRY = {
       "welcome": "welcome/SKILL.md",
       "ENCODE_Search": "ENCODE_Search/SKILL.md",
       ...
   }
   ```

2. **Loading** — When a skill becomes active, `AgentEngine._load_skill_text()`
   reads the Markdown and injects it into the first-pass LLM system prompt.
  Referenced `.md` files are automatically appended as included references.
  Resolution order is: skill-local relative path, `skills/` relative path,
  then `skills/shared/` by filename.

3. **Three-pass LLM flow** — Every user message runs through:
   - **Pass 1 (Think):** The LLM reads the skill text and emits structured
     tags (`[[DATA_CALL:…]]`, `[[PLOT:…]]`, etc.).
   - **Pass 2 (Retrieve):** The system executes data calls and collects
     results.
   - **Pass 3 (Analyze):** A second LLM pass reviews the retrieved data
     and produces the final user-facing answer (may include `[[PLOT:…]]`
     tags for visualization).

4. **Context injection** — `cortex/context_injection.py` automatically
   augments the user message with session state (e.g. `[CONTEXT: work_dir=…,
   sample=…]` for Dogme skills, or previous DataFrame rows for ENCODE
   follow-up queries).

---

## Skill File Structure

Every skill Markdown file should include these standard sections.  Not all
are required — include what is relevant to your skill.

### Header

```markdown
# Skill: Friendly Name (`skill_key`)
```

The key in parentheses must match the `SKILLS_REGISTRY` key exactly.

### Description

A short paragraph explaining what the skill does and when it applies.

### Skill Scope & Routing

Follows the pattern defined in `skills/shared/SKILL_ROUTING_PATTERN.md`:

```markdown
## Skill Scope & Routing

### ✅ This Skill Handles:
- Topic or question type
- "Example user question"

### ❌ This Skill Does NOT Handle:
- **Topic** → [[SKILL_SWITCH_TO: target_skill]]
  - Example: "Question that should go elsewhere"

### 🔀 General Routing Rules:
- **New local data** → [[SKILL_SWITCH_TO: analyze_local_sample]]
- **ENCODE experiments** → [[SKILL_SWITCH_TO: ENCODE_Search]]
- **Job results** → [[SKILL_SWITCH_TO: analyze_job_results]]
- **General help** → [[SKILL_SWITCH_TO: welcome]]
```

When uncertain, prefer routing to the appropriate skill over refusing to
help.

### Inputs

Document required and optional parameters accepted by the skill (e.g.
`path`, `sample_name`, `reference_genome`).

### Plan Logic

Step-by-step execution flow showing which `[[DATA_CALL:…]]` tags to emit
and in what order.

### Important Rules

Critical constraints the LLM must follow (tag format, approval gates,
things to never do).

### Approval Gates

If the skill performs destructive or costly operations (downloads, job
submissions), document when `[[APPROVAL_NEEDED]]` must be emitted.
Read-only skills like `ENCODE_Search` skip this section.

### Plan Chains

Optional.  Defines multi-step workflows that can be triggered by keyword
matching.  See the **Plan Chains** section below.

---

## Tag System

Skills communicate with the execution layer through structured tags in the
LLM output.  Tags use **double brackets** and are parsed by `cortex/app.py`.

### `[[DATA_CALL:…]]`

Executes a tool call against a service or consortium.

```
[[DATA_CALL: consortium=encode, tool=search_by_biosample, biosample=K562, organism=human]]
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=/path/to/job]]
[[DATA_CALL: service=launchpad, tool=delete_job_data, run_uuid=UUID]]
[[DATA_CALL: service=edgepython, tool=load_data, counts_path=/path/to/counts.csv]]
```

**Routing prefixes:**
| Prefix | Service |
|--------|---------|
| `consortium=encode` | ENCODE Portal (Atlas) — search, metadata, files |
| `service=analyzer` | Analysis Engine — job results, file browsing |
| `service=edgepython` | edgePython — DE analysis, gene annotation |
| `service=launchpad` | Launchpad — job submission, monitoring, deletion |

### `[[PLOT:…]]`

Renders an interactive chart from a DataFrame already in the conversation.

```
[[PLOT: type=bar, df=DF1, x=assay_title, agg=count, title=Experiments by assay]]
[[PLOT: type=pie, df=DF1, x=biosample, title=Sample distribution]]
[[PLOT: type=scatter, df=DF2, x=read_length, y=mapped_reads, color=lab]]
```

**Supported chart types:** `histogram`, `scatter`, `bar`, `box`, `heatmap`,
`pie`.

**Parameter rules:**
- `df` — Must reference a valid DF from the conversation (e.g. `DF1`).
- `x` / `y` — Must be actual column names from that DataFrame.
- `color` — Optional categorical column for grouping.
- `agg` — For bar charts: `count`, `sum`, or `mean`.
- `title` — Short descriptive title.

### `[[SKILL_SWITCH_TO:…]]`

Routes the conversation to a different skill.

```
[[SKILL_SWITCH_TO: ENCODE_Search]]
[[SKILL_SWITCH_TO: analyze_job_results]]
```

### `[[APPROVAL_NEEDED]]`

Pauses execution and asks the user to confirm before proceeding.  Used
before downloads, job submissions, and other costly or irreversible
operations.

---

## Plan Chains

Plan chains let skill authors declare multi-step workflows directly in
their Markdown files.  The planner (`cortex/planner.py`) detects matching
chains at classify-time and injects the plan into the LLM context so that
a single user message can trigger a coordinated sequence of actions.

### Format

Add a `## Plan Chains` section at the end of a skill file:

```markdown
## Plan Chains

### chain_name
- description: Short description of the workflow
- trigger: group1_kw1|group1_kw2 + group2_kw1|group2_kw2
- steps:
  1. ACTION_TYPE: Step description
  2. ACTION_TYPE: Step description
- auto_approve: true
- plot_hint: Optional hint for the LLM about visualization
```

### Trigger Syntax

Triggers use keyword groups separated by `+`.  Within each group, keywords
are separated by `|`.  **All groups** must have at least one keyword present
in the user message for the chain to match.

```
trigger: search|get|find|query + plot|chart|visualize|bar|pie
```

This matches "get the K562 experiments and make a bar chart" because `get`
satisfies group 1 and `bar chart` satisfies group 2.  It does **not** match
"get the K562 experiments" (group 2 has no match).

### Step Action Types

| Action | Icon | Description |
|--------|------|-------------|
| `SEARCH_DATA` | 🔍 | Search or query a data source |
| `SEARCH_ENCODE` | 🔍 | Search ENCODE specifically |
| `FILTER_DATA` | 🔎 | Filter or subset existing data |
| `GENERATE_PLOT` | 📊 | Create a visualization |
| `DOWNLOAD_DATA` | ⬇️ | Download files |
| `SUBMIT_WORKFLOW` | 🚀 | Submit a pipeline job |
| `PARSE_OUTPUT_FILE` | 📄 | Read and parse an output file |
| `SUMMARIZE_QC` | 📋 | Summarize QC metrics |
| `INTERPRET_RESULTS` | 🧠 | Interpret analysis results |
| `WRITE_SUMMARY` | ✍️ | Write a summary report |
| `COMPARE_SAMPLES` | ⚖️ | Compare datasets or samples |

New action types can be added freely — they are display labels, not
hard-coded enums.

### Example: skills/ENCODE_Search/SKILL.md chains

```markdown
### search_and_visualize
- description: Search ENCODE and visualize results
- trigger: search|get|find|query|show|plot|chart|visualize|graph|make + plot|chart|visualize|graph|pie|histogram|bar|scatter|heatmap|distribution|by
- steps:
  1. SEARCH_DATA: Search ENCODE for the requested data
  2. GENERATE_PLOT: Create a chart from the results
- auto_approve: true
- plot_hint: Infer chart type and grouping column from the user's message

### visualize_existing
- description: Plot data from a previous query
- trigger: plot|chart|visualize|graph|pie|histogram|bar|scatter|heatmap|make + assay|biosample|type|target|lab|status|category|organism + this|it|the data|results|previous|existing|DF
- steps:
  1. GENERATE_PLOT: Generate chart from previous results
- auto_approve: true
- plot_hint: Use the most recent DF and infer chart type from message

### search_filter_visualize
- description: Search, filter, then visualize
- trigger: search|get|find|query + filter|only|exclude|subset|where|with + plot|chart|visualize|graph
- steps:
  1. SEARCH_DATA: Search ENCODE for broad results
  2. FILTER_DATA: Apply user-specified filters
  3. GENERATE_PLOT: Visualize filtered data
- auto_approve: true
- plot_hint: Apply filters before plotting
```

### How Chains Execute

1. `cortex/planner.py` calls `load_chains_for_skill()` and `match_chain()`
   during request classification.
2. If a chain matches, the request is classified as `CHAIN_MULTI_STEP`.
3. `cortex/app.py` shows the chain plan to the user as a progress message,
   then injects `[PLAN CHAIN: …]` context and a PLOT instruction into the
   LLM prompt.
4. The LLM emits both `[[DATA_CALL:…]]` and `[[PLOT:…]]` tags in a single
   turn, and the system executes them in sequence.

---

## Referenced File Inclusion

If a skill Markdown file contains a link to another `.md` file, the
loader resolves it relative to the skill file and shared references, then
automatically appends that file's contents under the
section marker `[INCLUDED REFERENCE: filename.md]`.

This allows shared reference material (e.g. output file parsing guides) to
live in one place and be included by multiple skills.

---

## Existing Skills

| Key | File | Purpose |
|-----|------|---------|
| `welcome` | welcome/SKILL.md | Entry point and router |
| `ENCODE_Search` | ENCODE_Search/SKILL.md | Search and browse ENCODE Portal |
| `ENCODE_LongRead` | ENCODE_LongRead/SKILL.md | Download ENCODE data + submit to Dogme |
| `run_dogme_dna` | run_dogme_dna/SKILL.md | DNA / Fiber-seq result interpretation |
| `run_dogme_rna` | run_dogme_rna/SKILL.md | Direct RNA result interpretation |
| `run_dogme_cdna` | run_dogme_cdna/SKILL.md | cDNA result interpretation |
| `analyze_local_sample` | analyze_local_sample/SKILL.md | Local data intake wizard |
| `analyze_job_results` | analyze_job_results/SKILL.md | Completed job router |
| `download_files` | download_files/SKILL.md | File download manager |
| `differential_expression` | differential_expression/SKILL.md | DE analysis via edgePython |
| `enrichment_analysis` | enrichment_analysis/SKILL.md | GO/pathway enrichment |
| `remote_execution` | remote_execution/SKILL.md | Remote SLURM execution |

**Shared references** (not registered as skills, but auto-included):
- `shared/SKILL_ROUTING_PATTERN.md` — Standard routing section template
- `shared/DOGME_QUICK_WORKFLOW_GUIDE.md` — Shared output file parsing guide

---

## Adding a New Skill

1. **Create the Markdown file** at `skills/<skill_key>/SKILL.md` following the structure above.
   At minimum include: header, description, scope & routing, and plan logic.

2. **Register it** in `cortex/config.py`:

   ```python
   SKILLS_REGISTRY = {
       ...
       "my_new_skill": "my_new_skill/SKILL.md",
   }
   ```

3. **Add routing rules** — update other skills' "Does NOT Handle" sections
   to route relevant queries to your new skill.  Follow the pattern in
  `skills/shared/SKILL_ROUTING_PATTERN.md`.

4. **(Optional) Add plan chains** — if the skill supports multi-step
   workflows, add a `## Plan Chains` section with trigger definitions.

5. **Test** — ask the agent questions that should activate your skill and
   verify that classification, routing, and tool execution work correctly.

### Tips

- Keep skill files focused.  One skill per domain area.
- Use the `[[SKILL_SWITCH_TO:…]]` tag liberally — it's better to route than
  to refuse.
- Document `[[DATA_CALL:…]]` examples with exact tag syntax.  The LLM is
  sensitive to formatting and will copy your examples.
- Include "wrong vs right" examples for tricky tag formats — this
  significantly reduces hallucination.
- For shared reference material, create a separate `.md` file and link to it
  with the `[file.md](file.md)` pattern for automatic inclusion.
