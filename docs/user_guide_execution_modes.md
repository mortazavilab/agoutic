# User Guide: Execution Modes

AGOUTIC supports two execution modes: **Local** and **Remote SLURM**. This guide explains when to use each, how to configure them, and how to monitor your jobs.

## When to Use Local Execution

| Scenario | Why Local |
|----------|-----------|
| Small datasets (< 1 GB) | No transfer overhead |
| Quick test runs | Faster startup, no queue wait |
| Local GPU available | Direct access, no SLURM scheduling |
| Debugging pipelines | Easier to inspect logs and outputs |
| Analyzer-heavy workflows | Analysis runs locally in Phase 1 |

## When to Use Remote SLURM

| Scenario | Why Remote SLURM |
|----------|------------------|
| Large datasets (> 10 GB) | Cluster storage and I/O |
| Long-running jobs (> 1 hour) | Dedicated compute, no local impact |
| High CPU/memory needs | Access to nodes with 64+ cores, 256+ GB RAM |
| GPU-intensive work | Multi-GPU nodes available |
| Batch processing | Submit many jobs at once |

## How to Switch Execution Modes

### Via the UI

Toggle the **Execution Mode** switch in the job staging panel:
- **Local** — runs on the AGOUTIC host
- **Remote SLURM** — submits to the configured SLURM cluster

### Via Cortex (Conversation)

Tell Cortex directly:

- _"Run this on localCluster"_
- _"Submit to the cluster with 8 CPUs and 32GB memory"_
- _"Run locally"_

Cortex will stage the job with the appropriate execution mode.

### Default Mode

Set your default mode in **Settings → Execution Preferences**. New jobs will use this mode unless you override per-job.

---

## SSH Profiles

SSH profiles store your connection details for remote clusters.

### Creating a Profile

1. Go to **Settings → SSH Profiles → Add Profile**
2. Enter:
   - **Nickname:** a friendly name (e.g., `localCluster`)
   - **Host:** cluster hostname
   - **Username:** your cluster username
   - **Auth Method:** `key_file` or `ssh_agent`
   - **Key File Path:** (only for key_file) path to your private key on the server
   - **Local Username:** required for brokered key-file access when the AGOUTIC service runs under a different Unix user
   - **Remote Base Path:** top-level remote folder AGOUTIC should manage
3. Click **Save**

### Testing a Profile

Click **Test Connection** to verify SSH connectivity. A successful test returns the remote hostname and remote user. If the profile uses `key_file + local_username`, unlock the profile first.

### Unlocking a Brokered Profile

For shared-user deployments that use `key_file` together with `local_username`, AGOUTIC starts a per-session local auth broker under your Unix account:

- Use **Unlock** in the SSH profile UI
- Enter your local Unix password
- AGOUTIC reuses that unlocked broker session for SSH checks, remote browsing, staging, and rsync transfers

### Managing Profiles

- **Edit:** Update host, username, or auth details
- **Delete:** Removes the profile (jobs that used it retain their audit history)
- You can have multiple profiles for different clusters or accounts

---

## SLURM Resources

When submitting to SLURM, you can configure these resource parameters:

| Parameter | What It Means | Example |
|-----------|--------------|---------|
| **Account** | Your SLURM allocation/billing account | `lab_group` |
| **Partition** | The queue/set of nodes to use | `standard`, `gpu`, `highmem` |
| **CPUs** | Number of CPU cores per task | `4` |
| **Memory (GB)** | RAM allocated to the job | `16` |
| **Walltime** | Maximum allowed run time | `04:00:00` (4 hours) |
| **GPUs** | Number of GPUs (0 = none) | `1` |

These are set as defaults per SSH profile and can be overridden per job.

**Tips:**
- Request only what you need — smaller requests get scheduled faster
- Check partition limits before requesting large resources
- Walltime is a hard limit — your job will be killed if it exceeds this

---

## Remote Base Path

Remote execution now uses a single `remote_base_path` per SSH profile.

| Derived Path | Purpose |
|------|---------|
| `{remote_base_path}` | Root AGOUTIC-managed folder for the profile |
| `{remote_base_path}/ref` | Cached reference assets |
| `{remote_base_path}/data` | Cached staged sample inputs |
| `{remote_base_path}/{project_slug}/workflowN` | Per-workflow remote run directory |

### Configuration

Set this in **Settings → SSH Profiles → (your profile)**.

**Recommendations:**
- Use a scratch or project filesystem with enough capacity for both staging and execution
- Ensure the remote account can create and write under the chosen root
- Keep the path stable so cache reuse and browse prompts remain predictable

---

## Result Destination

Control where job results end up:

| Option | Behavior |
|--------|----------|
| **Local only** | Results are downloaded to the AGOUTIC host after completion. Remote copies are cleaned up. |
| **Remote only** | Results stay on the cluster. No automatic download. |
| **Both** | Results are downloaded AND kept on the cluster. |

Set your default in **Settings → Execution Preferences**. Override per-job when staging.

> **Phase 1 Note:** Analysis (Analyzer) runs locally. If you choose "remote only," you must manually trigger a download before running analysis on the results.

---

## Monitoring Remote Jobs and Staging

AGOUTIC now distinguishes between:

- **Stage-only tasks** — remote staging without job submission
- **Execution jobs** — actual SLURM workflow runs

Jobs progress through stages, displayed in the UI with status labels:

| Stage | What's Happening |
|-------|-----------------|
| `awaiting_details` | Cortex is collecting job parameters from you |
| `awaiting_approval` | Job is ready — waiting for your approval |
| `validating_connection` | AGOUTIC is checking SSH connectivity and prerequisites |
| `preparing_remote_dirs` | Remote directories are being prepared |
| `transferring_inputs` | Uploading or staging inputs to the cluster |
| `submitting_job` | Job is being submitted to SLURM |
| `queued` | Job submitted to SLURM — waiting in queue |
| `running` | Job is executing on the cluster |
| `collecting_outputs` | Gathering outputs from the remote run directory |
| `syncing_results` | Downloading results from the cluster |
| `completed` ✅ | Job finished successfully |
| `failed` ❌ | Something went wrong (check error details) |
| `cancelled` ⊘ | Job was cancelled by you or the system |

### What to Do at Each Stage

- **`awaiting_approval`** — Review parameters and approve or reject
- **`queued`** — Wait for the SLURM scheduler; you can check queue position in job details
- **`running`** — Monitor progress; elapsed time is shown in the UI
- **`failed`** — Click the job to see error details; consider adjusting resources and resubmitting
- **`transferring_*`** — Transfer progress is shown; if stalled, check your network connection

### Remote Browsing

Saved profile nicknames also work directly in chat:

- _"list files on localCluster"_
- _"list files in data on localCluster"_

Relative browse paths resolve under the profile's `remote_base_path`.

---

## Phase 1 Note

In Phase 1, the **Analyzer** component operates on local files only. This means:

- After a remote job completes, results must be on the local filesystem for analysis
- If `result_destination` is `local_only` or `both`, this happens automatically
- If `result_destination` is `remote_only`, download the results first (UI or API) before running analysis
- Future phases will support remote analysis directly on the cluster
