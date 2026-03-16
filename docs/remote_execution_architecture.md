# Remote Execution Architecture

## Overview

AGOUTIC supports dual execution modes:

| Mode | Description |
|------|-------------|
| **Local** | Jobs run on the machine hosting AGOUTIC (default) |
| **HPC3/SLURM** | Jobs are submitted to a remote SLURM cluster via SSH |

Both modes share the same prompt staging, approval, and monitoring workflows. The execution backend is selected per-job based on user preference or Cortex instruction.

## Backend Abstraction

Execution backends live in `launchpad/backends/` and implement the `ExecutionBackend` protocol:

```python
class ExecutionBackend(Protocol):
    async def submit(self, job: DogmeJob) -> str: ...
    async def poll_status(self, job: DogmeJob) -> JobStatus: ...
    async def cancel(self, job: DogmeJob) -> bool: ...
    async def fetch_results(self, job: DogmeJob) -> Path: ...
```

### Implementations

- **`LocalBackend`** — Runs Nextflow/commands directly on the host. No file transfer required.
- **`SlurmBackend`** — Connects via SSH, transfers inputs, submits `sbatch` jobs, polls `sacct`/`squeue`, retrieves outputs.

## Data Flow (Remote Execution)

```
User
  → Cortex (staged prompts, approval, state management)
    → Launchpad (backend selection based on execution_mode)
      → SlurmBackend
        → SSH connection (via SSHProfile)
          → rsync/sftp input files to remote_input_path
          → sbatch submit (SLURM script with SlurmDefaults)
          → poll squeue/sacct for completion
          → rsync/sftp results back to local
      → Results returned to Launchpad
    → UI displays status updates
```

## Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| **Cortex** | Prompt staging, user approval, conversation state, execution mode selection |
| **Launchpad** | Backend dispatch, file transfer orchestration, job monitoring, audit logging |
| **UI** | Display job stages, resource usage, transfer progress, logs |
| **Analyzer** | Post-run analysis on local files (Phase 1: local-only) |

## New Database Models

### SSHProfile

Stores SSH connection details per user. See [SSH Profile Security](ssh_profile_security.md).

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| user_id | UUID | Owner |
| nickname | str | Display name |
| host | str | Hostname or IP |
| port | int | SSH port (default 22) |
| username | str | Remote username |
| auth_method | enum | `key_file` or `ssh_agent` |
| key_file_path | str | Path to private key on server filesystem (nullable) |

### SlurmDefaults

Default SLURM resource parameters per SSH profile.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| ssh_profile_id | UUID | FK → SSHProfile |
| account | str | SLURM account/allocation |
| partition | str | Partition name |
| cpus | int | CPUs per task |
| memory_gb | int | Memory in GB |
| walltime | str | Max walltime (HH:MM:SS) |
| gpus | int | GPU count (0 = none) |
| extra_flags | str | Additional sbatch flags |

### RemotePathConfig

Remote filesystem paths per SSH profile.

| Column | Type | Description |
|--------|------|-------------|
| ssh_profile_id | UUID | FK → SSHProfile |
| remote_input_path | str | Where input files are staged |
| remote_work_path | str | Nextflow work directory |
| remote_output_path | str | Where results are written |

### RunAuditLog

Audit trail for all execution events.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| job_id | UUID | FK → DogmeJob |
| ssh_profile_id | UUID | FK → SSHProfile (nullable) |
| event | str | Event type |
| timestamp | datetime | When it occurred |
| details | JSON | Additional context |

### UserExecutionPreference

Per-user default execution settings.

| Column | Type | Description |
|--------|------|-------------|
| user_id | UUID | FK → User |
| default_mode | enum | `local` or `slurm` |
| default_ssh_profile_id | UUID | FK → SSHProfile (nullable) |
| result_destination | enum | `remote_only`, `local_only`, `both` |

## DogmeJob Extensions

New columns on the `DogmeJob` model for remote execution:

| Column | Type | Description |
|--------|------|-------------|
| execution_mode | enum | `local` or `slurm` |
| slurm_job_id | str | SLURM job ID (from sbatch) |
| slurm_account | str | Account used |
| slurm_partition | str | Partition used |
| slurm_cpus | int | CPUs allocated |
| slurm_memory_gb | int | Memory allocated |
| slurm_walltime | str | Walltime limit |
| slurm_gpus | int | GPUs allocated |
| remote_input_path | str | Actual remote input path for this run |
| remote_work_path | str | Actual remote work path |
| remote_output_path | str | Actual remote output path |
| transfer_state | enum | `pending`, `uploading`, `uploaded`, `downloading`, `downloaded`, `transfer_failed` |
| run_stage | enum | See stage state machine below |

## Stage State Machine

A job progresses through up to 13 stages:

```
awaiting_details
  → staged
    → approved
      → preparing_inputs
        → transferring_inputs
          → queued
            → running
              → transferring_outputs
                → post_processing
                  → completed

Any stage can transition to:
  → failed
  → cancelled
```

| Stage | Description |
|-------|-------------|
| `awaiting_details` | Cortex is gathering parameters from user |
| `staged` | Job is fully specified, awaiting approval |
| `approved` | User approved; execution about to begin |
| `preparing_inputs` | Local input files being assembled |
| `transferring_inputs` | rsync/sftp uploading inputs to cluster |
| `queued` | sbatch submitted; waiting in SLURM queue |
| `running` | SLURM job is executing |
| `transferring_outputs` | Downloading results from cluster |
| `post_processing` | Local post-processing (e.g., indexing) |
| `completed` | Job finished successfully |
| `failed` | Job failed at any stage |
| `cancelled` | User or system cancelled the job |

## File Transfer

- **Upload (inputs):** `rsync -az` or SFTP from local to `remote_input_path`
- **Download (outputs):** `rsync -az` or SFTP from `remote_output_path` to local
- **Tracking:** `transfer_state` on DogmeJob reflects current transfer status
- **Retry:** Failed transfers can be retried without resubmitting the SLURM job

## Phase 1 Limitation

> **Analyzer operates on local-accessible files only.** After a remote job completes, results must be transferred back to the local filesystem before analysis can run. This is handled automatically when `result_destination` is `local_only` or `both`. If `remote_only`, the user must trigger a manual download before running analysis.
