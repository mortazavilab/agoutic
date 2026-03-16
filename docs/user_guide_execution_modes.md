# User Guide: Execution Modes

AGOUTIC supports two execution modes: **Local** and **HPC3/SLURM**. This guide explains when to use each, how to configure them, and how to monitor your jobs.

## When to Use Local Execution

| Scenario | Why Local |
|----------|-----------|
| Small datasets (< 1 GB) | No transfer overhead |
| Quick test runs | Faster startup, no queue wait |
| Local GPU available | Direct access, no SLURM scheduling |
| Debugging pipelines | Easier to inspect logs and outputs |
| Analyzer-heavy workflows | Analysis runs locally in Phase 1 |

## When to Use HPC3/SLURM

| Scenario | Why HPC3 |
|----------|----------|
| Large datasets (> 10 GB) | Cluster storage and I/O |
| Long-running jobs (> 1 hour) | Dedicated compute, no local impact |
| High CPU/memory needs | Access to nodes with 64+ cores, 256+ GB RAM |
| GPU-intensive work | Multi-GPU nodes available |
| Batch processing | Submit many jobs at once |

## How to Switch Execution Modes

### Via the UI

Toggle the **Execution Mode** switch in the job staging panel:
- **Local** — runs on the AGOUTIC host
- **HPC3** — submits to the configured SLURM cluster

### Via Cortex (Conversation)

Tell Cortex directly:

- _"Run this on HPC3"_
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
   - **Nickname:** a friendly name (e.g., `HPC3`)
   - **Host:** cluster hostname
   - **Username:** your cluster username
   - **Auth Method:** `key_file` or `ssh_agent`
   - **Key File Path:** (only for key_file) path to your private key on the server
3. Click **Save**

### Testing a Profile

Click **Test Connection** to verify SSH connectivity. A successful test returns the remote hostname and latency.

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

## Remote Paths

Remote paths tell AGOUTIC where to store files on the cluster.

| Path | Purpose |
|------|---------|
| **Input Path** | Where input files are uploaded before the job runs |
| **Work Path** | Nextflow work directory (intermediate files) |
| **Output Path** | Where final results are written |

### Configuration

Set these in **Settings → SSH Profiles → (your profile) → Remote Paths**.

**Recommendations:**
- Use your home directory or a dedicated project directory
- Point `work_path` to a scratch/fast filesystem if available
- Ensure you have write permissions to all three paths

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

## Monitoring Remote Jobs

Jobs progress through stages, displayed in the UI with status labels:

| Stage | What's Happening |
|-------|-----------------|
| `awaiting_details` | Cortex is collecting job parameters from you |
| `staged` | Job is ready — waiting for your approval |
| `approved` | You approved — execution is starting |
| `preparing_inputs` | Assembling input files locally |
| `transferring_inputs` | Uploading inputs to the cluster |
| `queued` | Job submitted to SLURM — waiting in queue |
| `running` | Job is executing on the cluster |
| `transferring_outputs` | Downloading results from the cluster |
| `post_processing` | Running local post-processing steps |
| `completed` ✅ | Job finished successfully |
| `failed` ❌ | Something went wrong (check error details) |
| `cancelled` ⊘ | Job was cancelled by you or the system |

### What to Do at Each Stage

- **`staged`** — Review parameters and approve or reject
- **`queued`** — Wait for the SLURM scheduler; you can check queue position in job details
- **`running`** — Monitor progress; elapsed time is shown in the UI
- **`failed`** — Click the job to see error details; consider adjusting resources and resubmitting
- **`transferring_*`** — Transfer progress is shown; if stalled, check your network connection

---

## Phase 1 Note

In Phase 1, the **Analyzer** component operates on local files only. This means:

- After a remote job completes, results must be on the local filesystem for analysis
- If `result_destination` is `local_only` or `both`, this happens automatically
- If `result_destination` is `remote_only`, download the results first (UI or API) before running analysis
- Future phases will support remote analysis directly on the cluster
