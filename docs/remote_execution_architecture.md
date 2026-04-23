# Remote Execution Architecture

## Scope

This document describes the **current implemented architecture** for AGOUTIC
remote execution support for the `3.6.6` release series, with emphasis on the
parts that are already live in code:

- saved SSH profiles
- broker-mediated unlock and session reuse
- remote path layout derived from `remote_base_path`
- stage-only remote intake and cache reuse
- remote file browsing through Launchpad

It intentionally does **not** try to freeze the final shape of the full remote
submission and result-collection flow. That area is still being refined and is
expected to change in the next round of commits.

## Current Model

AGOUTIC supports two execution modes:

| Mode | Purpose |
|------|---------|
| `local` | Run DOGME/Nextflow on the AGOUTIC host |
| `slurm` | Use a saved SSH profile plus Launchpad's SLURM backend |

For remote work, Cortex does not talk to clusters directly. It routes all
remote operations through Launchpad.

## High-Level Components

| Component | Current responsibility |
|-----------|------------------------|
| `cortex` | Extract parameters, enforce approval/unlock preconditions, route remote browse and stage actions, maintain conversation/workflow state |
| `launchpad` REST API | SSH profile CRUD, unlock endpoints, stage-only endpoint, remote file listing endpoint, remote job submission endpoint |
| `launchpad` MCP server | Exposes Launchpad tools such as `stage_remote_sample` and `list_remote_files` to Cortex |
| `launchpad.backends.slurm_backend.SlurmBackend` | Derives remote paths, validates remote directories, stages references/data, browses remote files, submits and polls SLURM jobs |
| `launchpad.backends.ssh_manager` | Creates SSH connections and decides whether to use broker-backed or direct transport |
| `launchpad.backends.local_auth_sessions` | Starts, persists, discovers, expires, and closes per-profile local auth broker sessions |
| `launchpad.backends.local_user_broker` | Runs as the target local Unix user and executes `ssh` / `rsync` operations on behalf of Launchpad |
| UI | Lets users unlock profiles, approve remote staging, inspect remote browse results, and observe staging/job state |

## Remote Path Model

Remote filesystem layout is now derived from a **single root**:

- `remote_base_path`

The active remote roots are:

| Derived path | Meaning |
|-------------|---------|
| `{remote_base_path}` | Root for all AGOUTIC-managed remote artifacts on that profile |
| `{remote_base_path}/ref` | Reference cache root |
| `{remote_base_path}/data` | Input-data cache root |
| `{remote_base_path}/{project_slug}/workflowN` | Per-workflow remote working directory |
| `{remote_base_path}/{project_slug}/workflowN` | Per-workflow reconcile output root (with staged inputs under `workflowN/input`) |

This replaces the older architecture that treated remote input/work/output
paths as independent primary configuration.

### Browse Path Resolution

Remote browsing also uses `remote_base_path` as its anchor:

- `list files on localCluster` lists `{remote_base_path}`
- `list files in data on localCluster` lists `{remote_base_path}/data`
- absolute paths are allowed and remain absolute
- relative browse paths containing `..` are rejected

## SSH Authentication Model

### Supported Architecture for Remote Execution

For AGOUTIC-managed remote execution in this deployment, SSH access is expected
to flow through the **local auth broker** when profiles use:

- `auth_method = key_file`
- `local_username` set

That is the operational path used for:

- SSH connection tests after unlock
- remote staging
- remote file listing
- broker-preferred rsync transfers

In other words, the architecture AGOUTIC now documents and relies on for
shared-user deployments is:

1. user selects a saved SSH profile
2. user unlocks it with their **local Unix password**
3. Launchpad starts a broker process under `su <local_username>`
4. Launchpad stores session metadata under `AGOUTIC_DATA/runtime/local_auth`
5. later SSH and rsync operations reuse that broker session across Launchpad
   processes

### Unlock Flow

Unlock is handled by Launchpad REST endpoints:

- `GET /ssh-profiles/{profile_id}/auth-session`
- `POST /ssh-profiles/{profile_id}/auth-session`
- `DELETE /ssh-profiles/{profile_id}/auth-session`

The current flow is:

1. UI sends the local Unix password to `POST /ssh-profiles/{profile_id}/auth-session`
2. Launchpad loads the saved SSH profile
3. `LocalAuthSessionManager` launches `local_user_broker.py` via `su`
4. the broker binds a loopback TCP port and writes runtime files into
   `AGOUTIC_DATA/runtime/local_auth`
5. Launchpad records session metadata with an auth token in a `0600` JSON file
6. all later Launchpad processes can rediscover the session from that metadata

### Runtime Files and Hardening

Broker runtime artifacts now live under:

- `AGOUTIC_DATA/runtime/local_auth`

Important details:

- the directory is AGOUTIC-scoped, not global `/tmp`
- the directory must remain writable across the service-user / target-user
  boundary, so it uses sticky shared semantics rather than private `0700`
- session metadata files are created atomically with `0600`
- port and pid files are readable enough for the Launchpad service process to
  detect broker startup

### What the Broker Actually Does

The broker is deliberately small. It accepts authenticated JSON requests for:

- `ping`
- `shutdown`
- `ssh_run`
- `rsync_transfer`

It does not expose a general shell API to Cortex. Only Launchpad talks to it.

### Cross-Process Reuse

Launchpad's REST API and MCP server are separate processes. Because of that,
broker session state cannot live only in process memory.

The current implementation solves this by persisting broker session metadata to
the runtime directory so that:

- unlock can happen in the REST process
- remote browsing can happen in the MCP process
- staging can happen in either process
- all of them can reuse the same unlocked broker session

## SSH Profile Model

The current `SSHProfile` fields that matter to remote execution are:

| Field | Meaning |
|------|---------|
| `nickname` | User-facing profile name, also used in prompts like `on localCluster` |
| `ssh_host`, `ssh_port`, `ssh_username` | Remote endpoint identity |
| `auth_method` | `key_file` or `ssh_agent` |
| `key_file_path` | Path reference to the key file |
| `local_username` | Local Unix account that owns the key and must launch the broker |
| `default_slurm_account`, `default_slurm_partition`, `default_slurm_gpu_account`, `default_slurm_gpu_partition` | Approval defaults |
| `remote_base_path` | Canonical root for ref/data/project workflow paths and remote browsing |

### Architecture Note About Direct SSH Paths

The codebase still contains fallback paths for:

- readable direct key-file access without a broker
- `ssh_agent`

Those are implementation details of `SSHConnectionManager`. They are not the
recommended or primary architecture for AGOUTIC's current shared-user remote
execution workflow, which is broker-first and unlock-driven.

## Stage-Only Remote Intake

Stage-only remote intake is already a first-class flow.

### Entry Points

- Launchpad REST: `POST /remote/stage`
- Launchpad MCP: `stage_remote_sample`
- Cortex approval/task flow: `remote_action = stage_only`

### Purpose

This flow stages:

- reference assets into the remote `ref/` cache
- sample input data into the remote `data/` cache

without submitting a SLURM job.

### Current Stage Flow

1. Cortex extracts remote-stage intent and required parameters
2. Cortex requires a saved SSH profile and `remote_base_path`
3. if the profile is a brokered key-file profile, Cortex requires an active
   unlock session before approval can proceed
4. approval creates a dedicated `STAGING_TASK`, not a generic execution job
5. Launchpad validates `{remote_base_path}`, `ref/`, and `data/`
6. references and sample data are staged with cache reuse when possible
7. Cortex/UI render separate References and Data progress/status sections

### Remote Cache Persistence

Remote staging persists reusable metadata in:

| Model | Purpose |
|------|---------|
| `RemoteReferenceCache` | Reuse reference assets by `reference_id` |
| `RemoteInputCache` | Reuse staged sample input by fingerprint |
| `RemoteStagedSample` | Reuse a previously staged sample by sample identity and profile |

### Input Data Naming

Remote sample data paths are intentionally fingerprint-based, not semantic.

Current behavior:

- references stage under `ref/{reference_id}`
- sample data stages under `data/{input_fingerprint_prefix}`

This is deliberate cache behavior, not a UI naming bug.

## Remote File Browsing

Remote browsing is now an implemented, deterministic path.

### Entry Points

- Launchpad REST: `GET /remote/files`
- Launchpad MCP: `list_remote_files`
- Cortex chat override for prompts like:
   - `list files on localCluster`
   - `list files in data on localCluster`

### Current Browse Flow

1. Cortex detects explicit remote browse language before trusting LLM tags
2. Cortex resolves the SSH profile nickname to a saved profile
3. Cortex synthesizes a Launchpad `list_remote_files` tool call directly
4. Launchpad resolves the requested path against `remote_base_path`
5. `SlurmBackend.list_remote_files()` connects via the broker-aware SSH layer
6. the remote directory is listed with a one-level Python `os.scandir()` script
7. Cortex renders the results directly as a browse view instead of sending them
   through second-pass analysis

### Current Rendering Behavior

Successful remote browse results now:

- render as `Remote Files`
- show the resolved remote directory path
- show a table of `Name`, `Type`, and `Size`
- bypass the generic “query did not return the expected data” fallback

## Cortex Responsibilities for Remote Work

Cortex currently handles several remote-specific behaviors that are important to
the architecture:

- remote browse request extraction from plain language
- generic SSH nickname resolution instead of hardcoded profile names
- approval-time enforcement that locked brokered profiles must be unlocked
- stage-only planner support and `STAGING_TASK` lifecycle
- deterministic override of bad LLM clarifications or wrong skill switches for
  remote browsing

This means remote browsing and remote staging are no longer dependent on the
LLM correctly inventing the right tool call tags.

## Current REST and MCP Surface

### Launchpad REST

Relevant implemented endpoints:

- `POST /remote/stage`
- `GET /remote/files`
- `GET /ssh-profiles`
- `POST /ssh-profiles/{profile_id}/test`
- `GET /ssh-profiles/{profile_id}/auth-session`
- `POST /ssh-profiles/{profile_id}/auth-session`
- `DELETE /ssh-profiles/{profile_id}/auth-session`
- `POST /jobs/submit`

### Launchpad MCP

Relevant implemented tools:

- `stage_remote_sample`
- `list_remote_files`
- `submit_dogme_job`
- `check_nextflow_status`
- `get_job_logs`
- `cancel_slurm_job`

## State Models That Matter Today

### Current Remote Job / Task State

Remote execution uses `RunStage` values from `launchpad.backends.stage_machine`:

- `awaiting_details`
- `awaiting_approval`
- `validating_connection`
- `preparing_remote_dirs`
- `transferring_inputs`
- `submitting_job`
- `queued`
- `running`
- `collecting_outputs`
- `syncing_results`
- `completed`
- `failed`
- `cancelled`

### Important UI Distinction

Current UI semantics distinguish between:

- `STAGING_TASK` for stage-only remote intake
- `EXECUTION_JOB` for actual job execution

This distinction is deliberate and should remain in docs and UI copy.

## Data Model Notes

### Still Relevant

The following models are active in the current architecture:

- `SSHProfile`
- `SlurmDefaults`
- `DogmeJob` remote columns
- `RunAuditLog`
- `RemoteReferenceCache`
- `RemoteInputCache`
- `RemoteStagedSample`

### Legacy / No Longer Canonical

`RemotePathConfig` still exists in the schema, but it is no longer the primary
architecture for remote path derivation. New remote behavior should be described
in terms of `remote_base_path`.

## What This Document Deliberately Defers

The following areas are still in flux and should be updated in the next round
of commits rather than over-specified here:

- the final end-to-end remote job submission narrative
- the exact submission-time cache-preflight details in approval payloads
- the final result-destination and sync architecture for completed remote jobs
- any future analyzer-on-remote or remote-first postprocessing workflow

## Practical Summary

If you need the shortest accurate mental model of remote execution **today**, it
is this:

1. save an SSH profile with `remote_base_path`
2. if it uses `key_file + local_username`, unlock it first
3. unlock starts a broker under the target local Unix user
4. Launchpad reuses that broker for SSH commands and rsync transfers
5. remote staging writes into `{remote_base_path}/ref` and
   `{remote_base_path}/data`
6. remote browsing lists `{remote_base_path}` or a relative subpath under it
7. full remote submission exists, but its final architecture writeup is being
   deferred to the next change set