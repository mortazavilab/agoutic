# AGOUTIC Architecture — Visual Overview (v3.6.2)

> Presentation-ready diagrams for **AGOUTIC v3.6.2**, an agentic bioinformatics
> platform that orchestrates genomic data portal queries (ENCODE, IGVF),
> Nextflow pipeline execution (local and HPC/SLURM), differential expression,
> gene enrichment, and cross-workflow analysis through natural-language
> conversation.
>
> **Audience:** bioinformatics researchers, computational biologists, and
> reviewers evaluating the system architecture.
>
> **Tip:** Each diagram is self-contained and sized for a single PowerPoint
> slide or manuscript panel. Use the Mermaid CLI or a live editor to export
> SVG/PNG at 300 DPI.

---

## 1 — System Architecture

Six MCP micro-services communicate over stateless JSON-RPC 2.0.
The Streamlit UI connects only to Cortex; all downstream services are
abstracted behind the orchestrator.

```mermaid
graph LR
    User(["User"])

    subgraph ui ["UI  (Streamlit · 8501)"]
        Chat["Chat"]
        Approve["Approval\nGates"]
        Monitor["Job\nMonitor"]
        Results["Results\nBrowser"]
        Tasks["Task\nCenter"]
        Memories["Memory\nViewer"]
    end

    subgraph cortex ["Cortex  (Orchestrator · 8000)"]
        Pipeline["Chat Pipeline\n13 stages"]
        Planning["Planning\nmanifest → compose → validate"]
        Execution["Execution\nparallel-safe batches"]
        Memory["Memory Service\nauto-capture · injection"]
        Budget["Context Budget\ntiktoken allocation"]
    end

    subgraph services ["MCP Services"]
        LP["Launchpad\n8002 / 8003"]
        AN["Analyzer\n8004 / 8005"]
        ENCODE["ENCODE Portal\n8006"]
        IGVF["IGVF Portal\n8009"]
        EP["edgePython\n8007"]
    end

    subgraph backends ["Execution Backends"]
        Local["Local\nNextflow + Scripts"]
        SLURM["SLURM / HPC\nSSH · sbatch · rsync"]
    end

    User --> Chat
    ui -->|REST + cookie| cortex
    cortex -->|MCP JSON-RPC| services
    LP --> Local
    LP --> SLURM

    style ui fill:#2980b9,color:#fff,stroke:#2471a3
    style cortex fill:#1a5276,color:#fff,stroke:#154360
    style services fill:#117a65,color:#fff,stroke:#0e6655
    style backends fill:#6c3483,color:#fff,stroke:#5b2c6f
```

---

## 2 — Chat Pipeline (13 Stages)

A user message enters Cortex and flows through a priority-ordered pipeline
of self-registering stages. Any stage can short-circuit the pipeline
(e.g., `/memories` returns immediately at stage 230). The pipeline replaced
a monolithic 1,600-line function with 13 focused modules.

```mermaid
flowchart TB
    Msg["User message\nPOST /chat"]

    subgraph pipeline ["Chat Pipeline  (cortex/chat_pipeline.py)"]
        direction TB
        S100["100 — Setup\nuser · session · project"]
        S200["200 — Capabilities\nhelp · /skills · /workflows"]
        S210["210 — Prompt Inspection\nmodel debug · prompt dump"]
        S220["220 — DataFrame Command\nlist dfs · head DF · transform"]
        S230["230 — Memory Command\n/remember · /forget · /memories"]
        S240["240 — Memory Intent\n'remember that…' side-effects"]
        S300["300 — History\nlast 20 turns · DF map rebuild"]
        S400["400 — Context Prep\nmemory inject · state · budget"]
        S500["500 — Plan Detection\nheuristic classify → template or hybrid"]
        S600["600 — LLM First Pass\nskill prompt + tool contracts → response"]
        S700["700 — Tag Parsing\nDATA_CALL · PLOT · APPROVAL tags"]
        S800["800 — Overrides\npost-LLM sync/browse/early-exit"]
        S900["900 — Tool Execution\nMCP dispatch · ENCODE retry · chaining"]
        S1000["1000 — LLM Second Pass\nsummarise real data for user"]
        S1100["1100 — Response Assembly\nDF embed · plot specs · persist"]
    end

    Msg --> S100 --> S200 --> S210 --> S220 --> S230 --> S240
    S240 --> S300 --> S400 --> S500 --> S600 --> S700
    S700 --> S800 --> S900 --> S1000 --> S1100

    S200 -..->|short-circuit| Resp
    S220 -..->|short-circuit| Resp
    S230 -..->|short-circuit| Resp

    Resp(["Response\nAGENT_PLAN block + DFs + plots"])

    S1100 --> Resp

    style pipeline fill:#f8f9fa,color:#1a1a2e,stroke:#1a5276
    style Msg fill:#2980b9,color:#fff,stroke:#2471a3
    style Resp fill:#117a65,color:#fff,stroke:#0e6655
```

---

## 3 — Skill Manifests & Planning

Skills are defined as YAML manifests (`skills/<key>/manifest.yaml`) that
declare triggers, required services, MCP tool chains, and input/output types.
The planner attempts manifest-driven composition first, falling back to
deterministic templates for unmigrated flows.

```mermaid
flowchart LR
    Req["User\nrequest"]

    subgraph classify ["Plan Classifier"]
        Heuristic["Regex heuristics\n+ manifest triggers"]
        PlanType["plan_type\nrun_workflow · de_pipeline\nreconcile · remote_stage …"]
    end

    subgraph compose ["Plan Composer"]
        Manifest["SkillManifest\nYAML metadata"]
        Chain["MCP tool chain\nservice → tool → params"]
        Compose["Compose plan\nfrom manifest metadata"]
    end

    subgraph fallback ["Template Fallback"]
        Templates["11 deterministic\ntemplates"]
        Hybrid["Hybrid bridge\nLLM fragment composition\n6 non-core flows"]
    end

    subgraph validate ["Validation & Execution"]
        PlanVal["Plan Validator\nkinds · deps · cycles · scope"]
        Executor["Plan Executor\nparallel-safe batches\napproval gates"]
        Replan["Replanner\nfailure → SKIPPED dependents"]
    end

    Req --> Heuristic --> PlanType
    PlanType -->|manifest-backed| Manifest --> Chain --> Compose
    PlanType -->|unmigrated| Templates
    PlanType -->|non-core| Hybrid
    Hybrid -.->|fallback| Templates
    Compose --> PlanVal
    Templates --> PlanVal
    PlanVal --> Executor --> Replan

    style classify fill:#2980b9,color:#fff,stroke:#2471a3
    style compose fill:#1a5276,color:#fff,stroke:#154360
    style fallback fill:#b7950b,color:#fff,stroke:#9a7d0a
    style validate fill:#117a65,color:#fff,stroke:#0e6655
```

---

## 4 — Dual Consortium Data Access (ENCODE + IGVF)

AGOUTIC queries two major genomic data portals through dedicated MCP servers.
Both are registered in the consortium registry and accessed identically via
DATA_CALL tags. Natural-language routing auto-detects which portal to query.

```mermaid
graph TB
    User(["User\n'Search IGVF for K562 ATAC-seq'\n'Find ENCODE RNA-seq for liver'"])

    subgraph routing ["Natural-Language Routing"]
        Detect["Keyword detection\nIGVF accessions: IGVFDS / IGVFFI\nENCODE accessions: ENCSR / ENCFF\nportal name mentions"]
        Skill["Auto skill switch\nIGVF_Search or ENCODE_Search"]
    end

    subgraph encode ["ENCODE Portal  (port 8006)"]
        ET1["search_by_biosample"]
        ET2["search_by_assay"]
        ET3["get_experiment"]
        ET4["get_files_by_type"]
        ET5["download_file_url"]
        ET6["+ 12 more tools"]
    end

    subgraph igvf ["IGVF Portal  (port 8009)"]
        IT1["search_measurement_sets"]
        IT2["search_analysis_sets"]
        IT3["search_prediction_sets"]
        IT4["search_by_sample / by_assay"]
        IT5["search_files / genes"]
        IT6["get_dataset / file / gene"]
        IT7["get_file_download_url"]
        IT8["get_server_info"]
    end

    subgraph shared ["Shared Dispatch Infrastructure"]
        Alias["Tool + param alias correction"]
        Fallback["Hallucination regex repair"]
        Format["Result formatting + table columns"]
    end

    User --> Detect --> Skill
    Skill -->|ENCODE keywords| encode
    Skill -->|IGVF keywords| igvf
    encode --> shared
    igvf --> shared

    style encode fill:#2980b9,color:#fff,stroke:#2471a3
    style igvf fill:#6c3483,color:#fff,stroke:#5b2c6f
    style routing fill:#1a5276,color:#fff,stroke:#154360
    style shared fill:#117a65,color:#fff,stroke:#0e6655
```

---

## 5 — Skill System & Domain Coverage

Each skill lives in `skills/<key>/` with a `SKILL.md` prompt, an optional
`manifest.yaml` for planner metadata, and an optional `scripts/` directory
for allowlisted utilities. Skills are grouped by biological function and
mapped to backend MCP services.

```mermaid
graph TB
    subgraph data ["Data Access"]
        ES["ENCODE Search"]
        EL["ENCODE Long Read"]
        IS["IGVF Search"]
        DL["Download Files"]
    end

    subgraph exec ["Pipeline Execution"]
        DNA["Dogme DNA\nmethylation"]
        RNA["Dogme RNA\ndirect RNA"]
        CDNA["Dogme cDNA"]
        ALS["Local Sample\nIntake"]
        RE["Remote Execution\nHPC / SLURM"]
    end

    subgraph analysis ["Analysis & Interpretation"]
        AJR["Analyze Results\nQC · stats · BED"]
        DE["Differential\nExpression"]
        ENR["Enrichment\nGO · pathways"]
        RB["Reconcile BAMs\ncross-workflow"]
        XG["XGenePy"]
    end

    subgraph routing ["Service Backends"]
        ENCODE_S["ENCODE\nport 8006"]
        IGVF_S["IGVF\nport 8009"]
        LP_S["Launchpad\nport 8002"]
        AN_S["Analyzer\nport 8005"]
        EP_S["edgePython\nport 8007"]
    end

    ES & EL --> ENCODE_S
    IS --> IGVF_S
    ALS & RE & RB --> LP_S
    DNA & RNA & CDNA & AJR & ENR & XG --> AN_S
    DE --> EP_S

    style data fill:#2980b9,color:#fff,stroke:#2471a3
    style exec fill:#1a5276,color:#fff,stroke:#154360
    style analysis fill:#6c3483,color:#fff,stroke:#5b2c6f
    style routing fill:#117a65,color:#fff,stroke:#0e6655
```

---

## 6 — Execution Backends

The `ExecutionBackend` protocol provides a uniform interface for local and
remote job execution. Cortex and the UI remain backend-agnostic. Scripts run
through an allowlisted runner; remote staging runs as a durable background task.

```mermaid
classDiagram
    class ExecutionBackend {
        <<protocol>>
        +submit(run_uuid, params) str
        +check_status(run_uuid) JobStatus
        +cancel(run_uuid) bool
        +get_logs(run_uuid, limit) LogEntry[]
        +cleanup(run_uuid) bool
    }

    class LocalBackend {
        Nextflow subprocess
        SQLite progress polling
    }

    class SlurmBackend {
        SSH + asyncssh
        Resource validation
        sbatch generation
        Native Apptainer config
        Shared container cache
        rsync file transfer
        13-stage state machine
    }

    class ScriptRunner {
        Allowlist: deny-by-default
        Auto-discovery: skills/*/scripts/
        JSON output → DataFrame
    }

    class StagingWorker {
        Background asyncio.Task
        Durable DB persistence
        Survives Launchpad restarts
        Progress polling by Cortex
    }

    ExecutionBackend <|.. LocalBackend : implements
    ExecutionBackend <|.. SlurmBackend : implements
    LocalBackend -- ScriptRunner : run_type=script
    SlurmBackend -- StagingWorker : async file transfer
```

---

## 7 — HPC Remote Execution Lifecycle

Remote SLURM jobs follow a 13-stage state machine with enforced transitions.
Results are never marked complete until verified locally (fail-closed).
Staging now runs as a durable background task that survives HTTP timeouts
and Launchpad restarts.

```mermaid
stateDiagram-v2
    [*] --> awaiting_details

    awaiting_details --> awaiting_approval
    awaiting_approval --> validating_connection

    validating_connection --> preparing_remote_dirs
    preparing_remote_dirs --> transferring_inputs

    transferring_inputs --> submitting_job

    submitting_job --> queued
    queued --> running

    running --> collecting_outputs
    collecting_outputs --> syncing_results

    syncing_results --> completed

    awaiting_details --> cancelled
    awaiting_approval --> cancelled
    validating_connection --> failed
    transferring_inputs --> failed
    submitting_job --> failed
    running --> failed
    syncing_results --> failed

    state transferring_inputs {
        [*] --> background_rsync
        background_rsync --> progress_polling
        progress_polling --> transfer_done
        note right of background_rsync : Durable staging worker\nsurvives HTTP timeouts\nand Launchpad restarts
    }

    state completed {
        [*] --> outputs_verified
        outputs_verified --> auto_analysis
        note right of outputs_verified : Fail-closed:\nresults verified on disk\nbefore marking complete
    }
```

---

## 8 — Memory System

AGOUTIC maintains persistent memory across conversations with both project
and global scope. Memories are auto-captured from pipeline steps and results,
injected into LLM context with token-budgeted priority, and queryable via
slash commands or natural language.

```mermaid
flowchart TB
    subgraph sources ["Memory Sources"]
        Manual["/remember\n/annotate\n/remember-df"]
        AutoStep["Auto-capture\npipeline steps"]
        AutoResult["Auto-capture\nresults · DE · plots"]
        NL["Natural language\n'remember that X is…'"]
    end

    subgraph store ["Memory Store"]
        DB[("memories table\nuser_id · project_id\ncategory · scope")]
        Categories["8 categories\nresult · sample_annotation\npipeline_step · preference\nfinding · custom\ndataframe · plot"]
    end

    subgraph injection ["Context Injection"]
        Budget["Token budget\n~2,000 tokens default"]
        Priority["Priority order\n1. Pinned memories\n2. Sample annotations\n3. Recent results"]
        Block["MEMORY block\nprepended to LLM prompt"]
    end

    subgraph query ["Query & Management"]
        Slash["/memories · /forget\n/pin · /unpin\n/search-memories"]
        UI["Memories page\nfilter · recover · upgrade"]
        DFMem["Named DF recall\nhead c2c12DF\nplot c2c12DF by assay"]
    end

    Manual & AutoStep & AutoResult & NL --> DB
    DB --> Categories
    DB --> Budget --> Priority --> Block
    DB --> Slash & UI & DFMem

    style sources fill:#2980b9,color:#fff,stroke:#2471a3
    style store fill:#1a5276,color:#fff,stroke:#154360
    style injection fill:#117a65,color:#fff,stroke:#0e6655
    style query fill:#6c3483,color:#fff,stroke:#5b2c6f
```

---

## 9 — End-to-End Bioinformatics Workflow

A complete session from data discovery through pipeline execution to
biological interpretation. Diamonds are human approval gates. Safe steps
run in parallel; failures trigger the replanner.

```mermaid
flowchart TB
    Start(["User: 'Find RNA-seq for K562,\nrun Dogme RNA on the cluster,\nthen differential expression'"])

    subgraph p1 ["1  Data Discovery"]
        Search["Search ENCODE or IGVF\nbiosample · assay · target"]
        Browse["Interactive DataFrame\nfilter · sort · select"]
    end

    DL{"Approve\ndownload?"}

    subgraph p2 ["2  Stage to HPC"]
        Download["Download files"]
        Cache["Cache check\nreference already staged?"]
        Stage["Background rsync\ndurable staging worker"]
    end

    Submit{"Approve\nSLURM job?"}

    subgraph p3 ["3  HPC Execution"]
        Validate["Resource validation\nCPU · RAM · GPU · walltime"]
        Sbatch["sbatch submit\nNextflow + Apptainer"]
        Poll["SLURM monitoring\n13-stage tracking"]
        Sync["Result sync\nselective rsync to local"]
    end

    subgraph p4 ["4  Analysis"]
        QC["QC & stats parsing"]
        Reconcile["Reconcile BAMs\nacross workflows"]
        DE["Differential expression\ngrouped from abundance"]
        Enrich["GO & pathway\nenrichment"]
        Plots["Plots: volcano · MA\nheatmap · enrichment"]
        Summary["LLM interpretation\nresults → memory"]
    end

    Start --> Search --> Browse --> DL
    DL -->|Yes| Download --> Cache --> Stage --> Submit
    Submit -->|Yes| Validate --> Sbatch --> Poll --> Sync
    Sync --> QC --> Reconcile --> DE --> Enrich --> Plots --> Summary

    style p1 fill:#2980b9,color:#fff,stroke:#2471a3
    style p2 fill:#1a5276,color:#fff,stroke:#154360
    style p3 fill:#6c3483,color:#fff,stroke:#5b2c6f
    style p4 fill:#117a65,color:#fff,stroke:#0e6655
```

---

## 10 — Security Model

Security is enforced at every layer. No raw credentials are stored.
All destructive operations require explicit user approval.

```mermaid
flowchart LR
    subgraph auth ["Authentication"]
        OAuth["Google OAuth 2.0"]
        Session["Session tokens\n72h expiry · heartbeat"]
        Admin["Admin user approval"]
    end

    subgraph access ["Access Control"]
        RBAC["Project RBAC\nowner · editor · viewer"]
        Jail["User Jail\npaths ⊂ users/name/project/"]
        Traversal["Path traversal\nblocked"]
    end

    subgraph gates ["Approval Gates"]
        JobGate["Job submission"]
        DLGate["Data download"]
        RemoteGate["Remote execution"]
        ScriptGate["Script allowlist\ndeny-by-default"]
    end

    subgraph hpc ["HPC Isolation"]
        NoStore["No raw keys stored"]
        Broker["Auth broker\nper-session · 0600 · timeout"]
        Audit["Audit log\nno secrets recorded"]
    end

    subgraph validation ["Input Validation"]
        Schema["Tool param validation"]
        PlanCheck["Plan contract check"]
        Resource["SLURM resource limits"]
        ModeDrift["Mode/path drift\nrejection"]
    end

    auth --> access --> gates --> hpc
    gates --> validation

    style auth fill:#2980b9,color:#fff,stroke:#2471a3
    style access fill:#1a5276,color:#fff,stroke:#154360
    style gates fill:#b7950b,color:#fff,stroke:#9a7d0a
    style hpc fill:#6c3483,color:#fff,stroke:#5b2c6f
    style validation fill:#922b21,color:#fff,stroke:#7b241c
```

---

## 11 — Context Budget & Token Management

The LLM context window is allocated across six priority-ranked slots using
tiktoken for accurate token counting. This prevents context overflow and
ensures the most relevant information reaches the model.

```mermaid
flowchart LR
    subgraph window ["LLM Context Window  (32K tokens)"]
        direction TB
        Sys["System Prompt\n~2,000 tokens"]
        Skill["Skill Content\nactive SKILL.md"]
        Mem["Memory Injection\npinned > annotations > recent\n~2,000 token budget"]
        Tools["Tool Schemas\nactive service contracts"]
        History["Conversation History\noldest-first eviction"]
        Query["Current Query\naugmented message + state"]
    end

    Budget["ContextBudgetManager\ntiktoken cl100k_base\n6 priority slots\nhard/soft trim\nmin-token guarantees"]

    Budget --> Sys & Skill & Mem & Tools & History & Query

    style window fill:#f8f9fa,color:#1a1a2e,stroke:#1a5276
    style Budget fill:#1a5276,color:#fff,stroke:#154360
```

---

*Generated for AGOUTIC v3.6.2 — April 2026*
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
