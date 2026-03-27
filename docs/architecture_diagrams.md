# AGOUTIC Architecture — Visual Overview

> Eight diagrams illustrating the architecture of **AGOUTIC v3.4.9**, an agentic
> bioinformatics platform that orchestrates ENCODE data retrieval, Nextflow
> pipeline execution (local & HPC/SLURM), differential expression analysis,
> and gene enrichment through natural-language conversation.

---

## 1. Service Architecture

Five MCP (Model Context Protocol) micro-services communicate over stateless
JSON-RPC 2.0. The Streamlit UI talks exclusively to **Cortex**, which fans out
to specialised backends. No service ever bypasses the orchestrator. Cortex
itself is decomposed into focused modules — chat orchestration, planning,
execution, tool dispatch, and remote orchestration each live in their own file.

```mermaid
graph TB
    User["🧬 Bioinformatics User"]

    subgraph UI ["Streamlit UI · port 8501"]
        Chat["Chat Interface"]
        Approval["Approval Gates"]
        Monitor["Job Monitor"]
        Results["Results Browser"]
    end

    subgraph Cortex ["Cortex · Orchestrator · port 8000"]
        Agent["Agent Engine<br/><i>LLM reasoning + skill loading</i>"]
        TagParse["Tag Parser<br/><i>DATA_CALL · PLOT · APPROVAL<br/>tag extraction & correction</i>"]
        ToolDisp["Tool Dispatch<br/><i>MCP client lifecycle<br/>ENCODE retry · chaining</i>"]
        ChatDF["Chat DataFrames<br/><i>DF embedding · plot-spec<br/>resolution · DF-ID assignment</i>"]
        Planner["Planner<br/><i>11 templates + hybrid bridge<br/>fragment composition</i>"]
        PlanVal["Plan Validator<br/><i>contract checks before<br/>any step dispatch</i>"]
        Executor["Plan Executor<br/><i>parallel batches for safe kinds<br/>sequential for approvals</i>"]
        Replanner["Replanner<br/><i>failure recovery · SKIPPED<br/>dependent propagation</i>"]
        RemoteOrch["Remote Orchestration<br/><i>SSH profile · staging ·<br/>approval context · cache</i>"]
        Submission["Workflow Submission<br/><i>approval → submit dispatch</i>"]
        JobParams["Job Parameters<br/><i>conversation extraction<br/>path & mode resolution</i>"]
        JobPoll["Job Polling<br/><i>status polling ·<br/>auto-analysis trigger</i>"]
        State["Conversation State<br/><i>skill · project · sample context</i>"]
    end

    subgraph Services ["Downstream MCP Services"]
        LP["Launchpad<br/>port 8002/8003<br/><i>Job execution &<br/>backend dispatch</i>"]
        AN["Analyzer<br/>port 8004/8005<br/><i>QC · stats ·<br/>result parsing</i>"]
        AT["Atlas / ENCODE<br/>port 8006<br/><i>Consortium data<br/>search & download</i>"]
        EP["edgePython<br/>port 8007<br/><i>edgeR differential<br/>expression</i>"]
    end

    subgraph Backends ["Execution Backends"]
        Local["Local Backend<br/><i>Nextflow on workstation<br/>+ allowlisted scripts</i>"]
        SLURM["SLURM Backend<br/><i>SSH · sbatch · rsync<br/>Apptainer native</i>"]
    end

    Common["common/<br/><i>MCPHttpClient · logging ·<br/>gene annotation · DB</i>"]

    User --> Chat
    Chat -->|"REST + session cookie"| Agent
    Agent --> TagParse --> ToolDisp --> ChatDF
    Agent --> Planner
    Planner --> PlanVal --> Executor
    Executor --> Replanner
    Executor -->|"MCP JSON-RPC 2.0"| LP
    Executor -->|"MCP JSON-RPC 2.0"| AN
    Executor -->|"MCP JSON-RPC 2.0"| AT
    Executor -->|"MCP JSON-RPC 2.0"| EP
    RemoteOrch --> Submission --> JobParams
    Submission --> LP
    JobPoll --> LP
    JobPoll --> AN
    LP --> Local
    LP --> SLURM
    LP -.->|uses| Common
    AN -.->|uses| Common
    AT -.->|uses| Common

    style Cortex fill:#1a5276,color:#fff
    style UI fill:#2e86c1,color:#fff
    style Services fill:#117a65,color:#fff
    style Backends fill:#6c3483,color:#fff
```

---

## 2. Agent Intelligence — Two-Pass LLM Architecture

The agent uses a **two-pass architecture** that separates fast classification
from careful execution, ensuring the system never improvises on safety-critical
steps. Skill files (Markdown) encode domain workflows in a per-skill directory
layout (`skills/<key>/SKILL.md`); tool contracts give the LLM precise parameter
schemas. Multi-step requests now go through a **hybrid planning bridge** that
attempts LLM fragment composition first for six non-core flows, falling back to
deterministic templates on failure.

```mermaid
flowchart LR
    Msg["User<br/>message"]

    subgraph Pass1 ["Pass 1 — Classification (fast)"]
        Classify{"Heuristic<br/>classifier"}
        Info["INFORMATIONAL<br/><i>answer directly</i>"]
        Single["SINGLE_TOOL<br/><i>one MCP call</i>"]
        Multi["MULTI_STEP<br/><i>generate plan</i>"]
    end

    subgraph Pass2 ["Pass 2 — LLM Execution"]
        direction TB
        Prompt["System Prompt<br/>assembled from:"]
        Skills["🧬 Skills<br/><i>15 Markdown files</i><br/>ENCODE · Dogme DNA/RNA/cDNA<br/>DE · Enrichment · Remote Exec<br/>Reconcile BAMs · Long Read"]
        Contracts["📋 Tool Contracts<br/><i>JSON Schema per MCP tool</i>"]
        Context["📌 Context Injection<br/><i>project · sample · work_dir<br/>in-memory DataFrames</i>"]
        LLM["LLM Call<br/><i>Ollama / OpenAI-compatible</i>"]
    end

    subgraph Planning ["Plan Generation"]
        direction TB
        Hybrid{"Hybrid bridge?"}
        LLMPlan["LLM fragment<br/>composition<br/><i>6 non-core flows</i>"]
        Templates["Deterministic<br/>templates<br/><i>11 templates</i>"]
        Validate2["Plan contract<br/>validation<br/><i>kinds · deps · cycles<br/>scope · approval policy</i>"]
    end

    subgraph Output ["Response Processing"]
        TagParser["Tag Parser<br/><i>extract DATA_CALL · PLOT<br/>APPROVAL · fallback corrections</i>"]
        ToolDispatch["Tool Dispatch<br/><i>MCP client lifecycle<br/>ENCODE retry · chaining</i>"]
        DFEmbed["DataFrame Embedding<br/><i>DF-ID assignment<br/>plot-spec resolution</i>"]
    end

    Msg --> Classify
    Classify -->|question| Info
    Classify -->|single action| Single
    Classify -->|workflow| Multi
    Single --> Prompt
    Multi --> Planning
    Hybrid -->|non-core flow| LLMPlan --> Validate2
    Hybrid -->|core flow| Templates --> Validate2
    LLMPlan -.->|fallback| Templates
    Skills --> Prompt
    Contracts --> Prompt
    Context --> Prompt
    Prompt --> LLM
    LLM --> TagParser --> ToolDispatch --> DFEmbed

    style Pass1 fill:#1a5276,color:#fff
    style Pass2 fill:#117a65,color:#fff
    style Planning fill:#b7950b,color:#fff
    style Output fill:#6c3483,color:#fff
```

---

## 3. Execution Backend Abstraction & Script Runner

Cortex and the UI are **completely backend-agnostic** — the same
`ExecutionBackend` protocol drives both local Nextflow and remote SLURM
execution. Adding a new backend (e.g., AWS Batch) requires implementing
five methods without touching the rest of the stack.

Since v3.4.6, Launchpad also supports **allowlisted script execution** —
standalone Python utilities (e.g., `reconcile_bams`, `count_bed`) can run
via the `RUN_SCRIPT` plan step kind with a deny-by-default allowlist. Skills
automatically register their `scripts/*.py` at startup.

```mermaid
classDiagram
    class ExecutionBackend {
        <<protocol>>
        +submit(run_uuid, params) str
        +check_status(run_uuid) JobStatus
        +cancel(run_uuid) bool
        +get_logs(run_uuid, limit) list~LogEntry~
        +cleanup(run_uuid) bool
    }

    class SubmitParams {
        +project_id : str
        +user_id : str
        +sample_name : str
        +mode : DNA | RNA | CDNA
        +input_directory : str
        +reference_genome : list
        ──── remote fields (nullable) ────
        +ssh_profile_id : str?
        +slurm_account : str?
        +slurm_partition : str?
        +slurm_cpus : int?
        +slurm_memory_gb : int?
        +slurm_walltime : str?
        +remote_base_path : str?
        +result_destination : local | remote | both
    }

    class JobStatus {
        +run_uuid : str
        +status : PENDING | RUNNING | COMPLETED | FAILED
        +progress_percent : int
        +execution_mode : local | remote
        +run_stage : RunStage
        +slurm_job_id : str?
        +transfer_state : str?
    }

    class LocalBackend {
        +NextflowExecutor wrapper
        +Runs pipeline on workstation
        +Polls SQLite for progress
    }

    class SlurmBackend {
        +SSH connection management
        +Resource validation
        +sbatch script generation
        +rsync file transfer
        +Native Apptainer config
        +Shared container cache
        +SLURM state mapping
        +13-stage lifecycle tracking
    }

    class ScriptRunner {
        +Deny-by-default allowlist
        +Auto-discovery from skills/*/scripts/
        +Structured JSON output → DataFrame
        +Local-only execution
    }

    class PlanValidator {
        +Unknown step-kind rejection
        +Dependency cycle detection
        +Approval-policy enforcement
        +Project-scope mismatch check
        +Provenance metadata validation
    }

    ExecutionBackend <|.. LocalBackend : implements
    ExecutionBackend <|.. SlurmBackend : implements
    ExecutionBackend ..> SubmitParams : accepts
    ExecutionBackend ..> JobStatus : returns
    LocalBackend .. ScriptRunner : run_type=script
    PlanValidator ..> ExecutionBackend : gates dispatch
```

---

## 4. HPC Remote Execution — 13-Stage Lifecycle

Remote jobs follow a validated state machine with **13 stages** and enforced
transitions. The fail-closed design means results are never marked complete
until outputs are verified locally. Every stage transition is audit-logged.

```mermaid
stateDiagram-v2
    [*] --> awaiting_details : User describes job

    awaiting_details --> awaiting_approval : Parameters collected
    awaiting_approval --> validating_connection : User approves

    validating_connection --> preparing_remote_dirs : SSH OK
    preparing_remote_dirs --> transferring_inputs : Dirs created

    transferring_inputs --> submitting_job : rsync complete

    submitting_job --> queued : sbatch accepted
    queued --> running : SLURM scheduler starts job

    running --> collecting_outputs : Nextflow completes
    collecting_outputs --> syncing_results : Outputs identified

    syncing_results --> completed : rsync verified locally ✓

    awaiting_details --> cancelled : User cancels
    awaiting_approval --> cancelled : User cancels
    validating_connection --> failed : SSH connection error
    transferring_inputs --> failed : rsync error
    submitting_job --> failed : sbatch rejected
    running --> failed : SLURM job error
    syncing_results --> failed : Transfer error

    state completed {
        [*] --> outputs_downloaded
        outputs_downloaded --> auto_analysis : Cortex triggers Analyzer
    }

    note left of awaiting_approval
        Approval gate shows:
        • SSH profile & cluster
        • SLURM resources (CPU, RAM, GPU, walltime)
        • Staged reference & input paths
        • Estimated cost
    end note

    note right of syncing_results
        Fail-closed: jobs with
        result_destination = local | both
        stay here until outputs
        are verified on disk
    end note
```

---

## 5. End-to-End Bioinformatics Workflow

A typical AGOUTIC session — from consortium data discovery through pipeline
execution to biological interpretation — all driven by natural-language
conversation. Each arrow is an automated step; diamonds are human approval gates.
Safe discovery steps (LOCATE_DATA, SEARCH_ENCODE, CHECK_EXISTING) now execute
in **parallel batches** via `asyncio.gather()`, while approval-sensitive steps
remain sequential. Failed steps trigger the **replanner**, which marks dependents
as SKIPPED and adds recovery notes.

```mermaid
flowchart TB
    Start(["🧬 User: 'Find RNA-seq for<br/>K562, run Dogme RNA<br/>on the cluster, then DE'"])

    subgraph Phase1 ["Data Discovery (parallel batch)"]
        Search["Atlas / ENCODE MCP<br/>search_experiments<br/><i>organism · assay · biosample</i>"]
        Browse["Browse results<br/><i>interactive DataFrame in UI</i>"]
        Select["User selects files<br/><i>FASTQ / BAM accessions</i>"]
    end

    DL{"Approve<br/>download?"}

    subgraph Phase2 ["Download & Stage"]
        Download["Download ENCODE files<br/><i>via ENCODE portal URLs</i>"]
        Cache["Cache check<br/><i>reference genome already<br/>staged on cluster?</i>"]
        Stage["Stage inputs to HPC<br/><i>rsync to remote_base_path/<br/>user/project/data/</i>"]
    end

    Submit{"Approve<br/>SLURM<br/>submission?"}

    subgraph Phase3 ["HPC Execution"]
        Validate["Validate SLURM resources<br/><i>CPUs · RAM · walltime · GPUs<br/>partition · account whitelist</i>"]
        Sbatch["Generate & submit sbatch<br/><i>Nextflow + native Apptainer<br/>shared container cache</i>"]
        Poll["Monitor SLURM job<br/><i>scontrol / sacct polling<br/>13-stage tracking</i>"]
        Sync["Sync results to local<br/><i>selective rsync: annot/ bams/<br/>bedMethyl/ kallisto/ stats/</i>"]
    end

    subgraph Phase4 ["Analysis & Interpretation"]
        QC["Parse QC & stats<br/><i>Analyzer MCP</i>"]
        Reconcile["Reconcile BAMs<br/><i>allowlisted script<br/>cross-workflow merge</i>"]
        DE["Differential expression<br/><i>edgePython MCP · edgeR</i>"]
        Enrich["GO & pathway enrichment<br/><i>Analyzer MCP</i>"]
        Plots["Generate plots<br/><i>volcano · MA · heatmap<br/>BED chr counts · enrichment</i>"]
        Summary["Natural-language summary<br/><i>LLM interprets results</i>"]
    end

    subgraph Recovery ["Failure Recovery"]
        Replanner["Replanner<br/><i>mark dependents SKIPPED<br/>add recovery notes</i>"]
    end

    Start --> Search --> Browse --> Select --> DL
    DL -->|Yes| Download --> Cache --> Stage --> Submit
    DL -->|No| Browse

    Submit -->|Yes| Validate --> Sbatch --> Poll --> Sync
    Submit -->|No| Stage

    Sync --> QC --> Reconcile --> DE --> Enrich --> Plots --> Summary

    Validate -.->|failure| Replanner
    Poll -.->|failure| Replanner
    Sync -.->|failure| Replanner

    style Phase1 fill:#1a5276,color:#fff
    style Phase2 fill:#117a65,color:#fff
    style Phase3 fill:#6c3483,color:#fff
    style Phase4 fill:#b7950b,color:#fff
    style Recovery fill:#922b21,color:#fff
```

---

## 6. Security & Multi-Layer Access Control

AGOUTIC enforces security at every layer — from OAuth login through
per-user filesystem jails to credential-free HPC submission.
No raw secrets are ever stored; all sensitive operations require explicit
approval gates.

```mermaid
flowchart TB
    subgraph Auth ["Authentication"]
        OAuth["Google OAuth 2.0<br/><i>login → consent → callback</i>"]
        Session["Session tokens<br/><i>24h expiry · bearer auth</i>"]
        Admin["Admin approval<br/><i>new users require admin OK</i>"]
    end

    subgraph Access ["Access Control"]
        RBAC["Project RBAC<br/><i>owner · editor · viewer</i>"]
        Jail["User Jail<br/><i>all paths scoped to<br/>users/{name}/{project}/</i>"]
        Traversal["Path traversal blocked<br/><i>reject .. and / in slugs<br/>resolved path ∈ jail dir</i>"]
    end

    subgraph Approval ["Approval Gates"]
        JobApproval["Job submission<br/><i>review params before run</i>"]
        DLApproval["Data download<br/><i>confirm files & size</i>"]
        RemoteApproval["Remote execution<br/><i>cluster · resources · cost</i>"]
    end

    subgraph HPC_Sec ["HPC Credential Isolation"]
        NoStore["No raw keys stored<br/><i>only filesystem path refs</i>"]
        Broker["Auth broker<br/><i>per-session · scoped tokens<br/>0600 permissions</i>"]
        Timeout["Broker timeout<br/><i>no indefinite sessions</i>"]
        Audit["Run audit log<br/><i>user · profile · resources<br/>timestamps · no secrets</i>"]
    end

    subgraph Validation ["Input Validation"]
        Schema["Tool schema validation<br/><i>params checked before<br/>MCP dispatch</i>"]
        PlanContract["Plan contract validation<br/><i>step kinds · dependencies<br/>cycles · scope · approvals</i>"]
        Resource["SLURM resource limits<br/><i>max CPU · RAM · walltime<br/>partition whitelist</i>"]
        RemotePath["Remote path validation<br/><i>existence · writability<br/>traversal blocked</i>"]
        ScriptAllow["Script allowlist<br/><i>deny-by-default execution<br/>auto-discovered from skills/</i>"]
        Sanitize["Filename sanitization<br/><i>reject unsafe characters</i>"]
        ModeDrift["Mode/path drift rejection<br/><i>approval-time safety checks<br/>block execution-mode mismatch</i>"]
    end

    OAuth --> Session --> RBAC
    RBAC --> Jail --> Traversal
    JobApproval --> Schema
    JobApproval --> PlanContract
    RemoteApproval --> Resource
    RemoteApproval --> ModeDrift
    RemoteApproval --> NoStore --> Broker --> Timeout
    Broker --> Audit
    Schema --> RemotePath --> ScriptAllow --> Sanitize

    style Auth fill:#1a5276,color:#fff
    style Access fill:#117a65,color:#fff
    style Approval fill:#b7950b,color:#fff
    style HPC_Sec fill:#6c3483,color:#fff
    style Validation fill:#922b21,color:#fff
```

---

## 7. Skill System & Domain Coverage

AGOUTIC's capabilities are encoded as **skills** — human-readable Markdown
files that define domain workflows, valid tool calls, and prompting strategies.
Each skill lives in its own directory (`skills/<key>/SKILL.md`) and can include
embedded Python scripts that execute via the allowlisted script runner.
The skill registry maps every skill to a backend service so the agent knows
where to dispatch tool calls.

```mermaid
graph TB
    subgraph SkillSystem ["Skill System · 15 skills in skills/<key>/SKILL.md"]
        direction TB
        subgraph DataAccess ["Data Access"]
            ES["ENCODE_Search<br/><i>biosample · assay · target<br/>experiment search & filtering</i>"]
            EL["ENCODE_LongRead<br/><i>long-read-specific<br/>ENCODE queries</i>"]
            DL["download_files<br/><i>ENCODE file download<br/>to local project dir</i>"]
        end

        subgraph PipelineExec ["Pipeline Execution"]
            DNA["run_dogme_dna<br/><i>DNA methylation<br/>Nanopore pipeline</i>"]
            RNA["run_dogme_rna<br/><i>RNA-seq / direct RNA<br/>Nanopore pipeline</i>"]
            CDNA["run_dogme_cdna<br/><i>cDNA sequencing<br/>Nanopore pipeline</i>"]
            ALS["analyze_local_sample<br/><i>local sample intake<br/>param collection → submit</i>"]
            RE["remote_execution<br/><i>HPC/SLURM staging<br/>SSH profile · sbatch</i>"]
        end

        subgraph Analysis ["Analysis & Interpretation"]
            AJR["analyze_job_results<br/><i>QC · stats · file parsing<br/>📜 scripts/count_bed.py</i>"]
            DE["differential_expression<br/><i>edgeR via edgePython<br/>volcano · MA plots</i>"]
            ENR["enrichment_analysis<br/><i>GO · pathway enrichment<br/>dot plots</i>"]
            RB["reconcile_bams<br/><i>cross-workflow BAM merge<br/>📜 scripts/reconcile_bams.py<br/>📜 scripts/check_workflow_references.py</i>"]
        end

        WEL["welcome<br/><i>entry point · routing</i>"]

        Shared["shared/<br/><i>DOGME_QUICK_WORKFLOW_GUIDE.md<br/>SKILL_ROUTING_PATTERN.md</i>"]
    end

    subgraph Routing ["Service Routing · get_source_for_skill()"]
        ENCODE_SVC["🧬 ENCODE Portal<br/>port 8006"]
        LP_SVC["🚀 Launchpad<br/>port 8002"]
        AN_SVC["📊 Analyzer<br/>port 8005"]
        EP_SVC["📈 edgePython<br/>port 8007"]
    end

    ES --> ENCODE_SVC
    EL --> ENCODE_SVC
    ALS --> LP_SVC
    RE --> LP_SVC
    RB --> LP_SVC
    DNA --> AN_SVC
    RNA --> AN_SVC
    CDNA --> AN_SVC
    AJR --> AN_SVC
    ENR --> AN_SVC
    DE --> EP_SVC

    style DataAccess fill:#1a5276,color:#fff
    style PipelineExec fill:#117a65,color:#fff
    style Analysis fill:#6c3483,color:#fff
    style Routing fill:#b7950b,color:#fff
```

---

## 8. Conversation Message Lifecycle

How a single user message flows through Cortex — from chat input to persisted
response. The two-pass LLM architecture is visible here: the first pass
generates a plan or tool calls, the second pass summarises real data results.
Each numbered box is a distinct module; no single file handles the full path.

```mermaid
sequenceDiagram
    actor User
    participant UI as Streamlit UI
    participant App as cortex/app.py<br/>chat_with_agent()
    participant CI as context_injection<br/>+ conversation_state
    participant AE as AgentEngine<br/>(LLM Pass 1)
    participant TP as tag_parser
    participant TD as tool_dispatch
    participant MCP as MCP Services<br/>(Launchpad · Analyzer<br/>ENCODE · edgePython)
    participant DF as chat_dataframes
    participant AE2 as AgentEngine<br/>(LLM Pass 2)
    participant DB as Database

    User->>UI: "Find K562 RNA-seq experiments"
    UI->>App: POST /chat {project_id, message, skill}

    Note over App: Step 1: Save user message
    App->>DB: create USER_MESSAGE block

    Note over App: Step 2: Build conversation history
    App->>DB: fetch last 20 turns (40 messages)

    Note over App: Step 3: Context injection
    App->>CI: _inject_job_context(message, skill, history)
    CI-->>App: augmented_message + injected DFs
    App->>CI: _build_conversation_state(skill, history)
    CI-->>App: ConversationState JSON

    Note over App: Step 4: First-pass LLM
    App->>AE: engine.think(augmented_msg, skill, history)
    Note over AE: Loads skills/<key>/SKILL.md<br/>+ tool contracts as system prompt
    AE-->>App: raw_response + token usage

    Note over App: Step 5: Tag parsing & correction
    App->>TP: parse_data_tags(raw_response)
    App->>TP: parse_plot_tags(raw_response)
    App->>TP: parse_approval_tag(raw_response)
    TP-->>App: DATA_CALL matches + PLOT specs + approval flag

    Note over App: Step 6: Tool dispatch
    App->>TD: build_calls_by_source(matches)
    TD-->>App: calls_by_source {service → [calls]}
    App->>TD: execute_tool_calls(calls_by_source)
    TD->>MCP: MCPHttpClient.call_tool() per service
    MCP-->>TD: JSON-RPC results
    TD-->>App: all_results + formatted_data

    Note over App: Step 7: DataFrame embedding
    App->>DF: extract_embedded_dataframes(results)
    App->>DF: assign_df_ids(dataframes, history)
    DF-->>App: DF1, DF2, … with metadata

    Note over App: Step 8: Second-pass LLM (if real data)
    App->>AE2: engine.analyze_results(msg, markdown, data)
    Note over AE2: Summarise/filter results<br/>for user's question
    AE2-->>App: analyzed_response

    Note over App: Step 9: Persist response
    App->>DB: create AGENT_PLAN block<br/>{markdown, DFs, plots, state, tokens}
    opt If approval needed
        App->>DB: create APPROVAL_GATE block (PENDING)
    end

    App-->>UI: {user_block, agent_block, gate_block?}
    UI-->>User: Rendered chat + DataFrames + plots

    Note over UI: Approval gates render as<br/>interactive review panels
```

---

*Generated for AGOUTIC v3.4.9 — March 2026*
