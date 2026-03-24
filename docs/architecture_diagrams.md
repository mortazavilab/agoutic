# AGOUTIC Architecture — Visual Overview

> Six diagrams illustrating the architecture of **AGOUTIC v3.4.3**, an agentic
> bioinformatics platform that orchestrates ENCODE data retrieval, Nextflow
> pipeline execution (local & HPC/SLURM), differential expression analysis,
> and gene enrichment through natural-language conversation.

---

## 1. Service Architecture

Five MCP (Model Context Protocol) micro-services communicate over stateless
JSON-RPC 2.0. The Streamlit UI talks exclusively to **Cortex**, which fans out
to specialised backends. No service ever bypasses the orchestrator.

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
        Planner["Plan Generator<br/><i>10 deterministic templates</i>"]
        Executor["Plan Executor<br/><i>step-by-step dispatch</i>"]
        State["Conversation State<br/><i>skill · project · sample context</i>"]
    end

    subgraph Services ["Downstream MCP Services"]
        LP["Launchpad<br/>port 8002/8003<br/><i>Job execution &<br/>backend dispatch</i>"]
        AN["Analyzer<br/>port 8004/8005<br/><i>QC · stats ·<br/>result parsing</i>"]
        AT["Atlas / ENCODE<br/>port 8006<br/><i>Consortium data<br/>search & download</i>"]
        EP["edgePython<br/>port 8007<br/><i>edgeR differential<br/>expression</i>"]
    end

    subgraph Backends ["Execution Backends"]
        Local["Local Backend<br/><i>Nextflow on workstation</i>"]
        SLURM["SLURM Backend<br/><i>SSH · sbatch · rsync</i>"]
    end

    Common["common/<br/><i>MCPHttpClient · logging ·<br/>gene annotation · DB</i>"]

    User --> Chat
    Chat -->|"REST + session cookie"| Agent
    Agent --> Planner
    Planner --> Executor
    Executor -->|"MCP JSON-RPC 2.0"| LP
    Executor -->|"MCP JSON-RPC 2.0"| AN
    Executor -->|"MCP JSON-RPC 2.0"| AT
    Executor -->|"MCP JSON-RPC 2.0"| EP
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
steps. Skill files (Markdown) encode domain workflows; tool contracts give the
LLM precise parameter schemas.

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
        Skills["🧬 Skills<br/><i>14 Markdown files</i><br/>ENCODE · Dogme DNA/RNA/cDNA<br/>DE · Enrichment · Remote Exec"]
        Contracts["📋 Tool Contracts<br/><i>JSON Schema per MCP tool</i>"]
        Context["📌 Context Injection<br/><i>project · sample · work_dir<br/>in-memory DataFrames</i>"]
        LLM["LLM Call<br/><i>Ollama / OpenAI-compatible</i>"]
    end

    subgraph Output ["Response Processing"]
        Validate["Validator<br/><i>strip malformed calls<br/>deduplicate approvals<br/>check tool names</i>"]
        DataCall["[[DATA_CALL]]<br/>→ MCP dispatch"]
        ApprovalTag["[[APPROVAL_NEEDED]]<br/>→ UI gate"]
        Plot["[[PLOT:…]]<br/>→ inline chart"]
    end

    Msg --> Classify
    Classify -->|question| Info
    Classify -->|single action| Single
    Classify -->|workflow| Multi
    Single --> Prompt
    Multi --> Prompt
    Skills --> Prompt
    Contracts --> Prompt
    Context --> Prompt
    Prompt --> LLM
    LLM --> Validate
    Validate --> DataCall
    Validate --> ApprovalTag
    Validate --> Plot

    style Pass1 fill:#1a5276,color:#fff
    style Pass2 fill:#117a65,color:#fff
    style Output fill:#6c3483,color:#fff
```

---

## 3. Execution Backend Abstraction

Cortex and the UI are **completely backend-agnostic** — the same
`ExecutionBackend` protocol drives both local Nextflow and remote SLURM
execution. Adding a new backend (e.g., AWS Batch) requires implementing
five methods without touching the rest of the stack.

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
        +SLURM state mapping
        +13-stage lifecycle tracking
    }

    ExecutionBackend <|.. LocalBackend : implements
    ExecutionBackend <|.. SlurmBackend : implements
    ExecutionBackend ..> SubmitParams : accepts
    ExecutionBackend ..> JobStatus : returns
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

```mermaid
flowchart TB
    Start(["🧬 User: 'Find RNA-seq for<br/>K562, run Dogme RNA<br/>on the cluster, then DE'"])

    subgraph Phase1 ["Data Discovery"]
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
        Sbatch["Generate & submit sbatch<br/><i>Nextflow + Apptainer<br/>Dogme RNA pipeline</i>"]
        Poll["Monitor SLURM job<br/><i>scontrol / sacct polling<br/>13-stage tracking</i>"]
        Sync["Sync results to local<br/><i>selective rsync: annot/ bams/<br/>bedMethyl/ kallisto/ stats/</i>"]
    end

    subgraph Phase4 ["Analysis & Interpretation"]
        QC["Parse QC & stats<br/><i>Analyzer MCP</i>"]
        DE["Differential expression<br/><i>edgePython MCP · edgeR</i>"]
        Enrich["GO & pathway enrichment<br/><i>Analyzer MCP</i>"]
        Plots["Generate plots<br/><i>volcano · MA · heatmap<br/>enrichment dot plots</i>"]
        Summary["Natural-language summary<br/><i>LLM interprets results</i>"]
    end

    Start --> Search --> Browse --> Select --> DL
    DL -->|Yes| Download --> Cache --> Stage --> Submit
    DL -->|No| Browse

    Submit -->|Yes| Validate --> Sbatch --> Poll --> Sync
    Submit -->|No| Stage

    Sync --> QC --> DE --> Enrich --> Plots --> Summary

    style Phase1 fill:#1a5276,color:#fff
    style Phase2 fill:#117a65,color:#fff
    style Phase3 fill:#6c3483,color:#fff
    style Phase4 fill:#b7950b,color:#fff
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
        Resource["SLURM resource limits<br/><i>max CPU · RAM · walltime<br/>partition whitelist</i>"]
        RemotePath["Remote path validation<br/><i>existence · writability<br/>traversal blocked</i>"]
        Sanitize["Filename sanitization<br/><i>reject unsafe characters</i>"]
    end

    OAuth --> Session --> RBAC
    RBAC --> Jail --> Traversal
    JobApproval --> Schema
    RemoteApproval --> Resource
    RemoteApproval --> NoStore --> Broker --> Timeout
    Broker --> Audit
    Schema --> RemotePath --> Sanitize

    style Auth fill:#1a5276,color:#fff
    style Access fill:#117a65,color:#fff
    style Approval fill:#b7950b,color:#fff
    style HPC_Sec fill:#6c3483,color:#fff
    style Validation fill:#922b21,color:#fff
```

---

*Generated for AGOUTIC v3.4.3 — March 2026*
