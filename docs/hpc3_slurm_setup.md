# HPC3 / SLURM Setup Guide

## Prerequisites

- SSH access to the HPC3 cluster (or your target SLURM cluster)
- A valid SLURM account/allocation on the cluster
- Nextflow installed and available in your `$PATH` on the cluster
- AGOUTIC running locally with a user account

## Step 1: Create an SSH Key Pair (if using key_file auth)

Skip this step if you plan to use `ssh_agent` authentication.

```bash
# Generate an Ed25519 key pair
ssh-keygen -t ed25519 -f ~/.ssh/agoutic_hpc3 -C "agoutic@hpc3"

# Restrict permissions
chmod 600 ~/.ssh/agoutic_hpc3
chmod 644 ~/.ssh/agoutic_hpc3.pub
```

## Step 2: Copy the Public Key to the Cluster

```bash
ssh-copy-id -i ~/.ssh/agoutic_hpc3.pub youruser@hpc3.rc.uci.edu
```

Verify you can connect without a password prompt:

```bash
ssh -i ~/.ssh/agoutic_hpc3 youruser@hpc3.rc.uci.edu "hostname"
```

## Step 3: Create an SSH Profile in AGOUTIC

### Via the UI

1. Navigate to **Settings → SSH Profiles**
2. Click **Add Profile**
3. Fill in:
   - **Nickname:** `HPC3`
   - **Host:** `hpc3.rc.uci.edu`
   - **Port:** `22`
   - **Username:** your cluster username
   - **Auth Method:** `key_file` or `ssh_agent`
   - **Key File Path** (if key_file): `/path/to/agoutic_hpc3`
4. Click **Save**

### Via the API

```bash
curl -X POST http://localhost:8000/api/ssh-profiles \
  -H "Content-Type: application/json" \
  -d '{
    "nickname": "HPC3",
    "host": "hpc3.rc.uci.edu",
    "port": 22,
    "username": "youruser",
    "auth_method": "key_file",
    "key_file_path": "/home/agoutic/.ssh/agoutic_hpc3"
  }'
```

## Step 4: Test the Connection

### Via the UI

Click **Test Connection** next to the SSH profile. A successful test shows a green checkmark and the remote hostname.

### Via the API

```bash
curl -X POST http://localhost:8000/api/ssh-profiles/{profile_id}/test
```

Expected response:

```json
{"status": "ok", "hostname": "hpc3.rc.uci.edu", "latency_ms": 45}
```

## Step 5: Configure SLURM Defaults

Set default resource parameters so you don't have to specify them for every job.

### Via the UI

1. Navigate to **Settings → SSH Profiles → HPC3 → SLURM Defaults**
2. Configure:
   - **Account:** your SLURM account (e.g., `lab_group`)
   - **Partition:** target partition (e.g., `standard`, `gpu`)
   - **CPUs:** default CPUs per task (e.g., `4`)
   - **Memory (GB):** default memory (e.g., `16`)
   - **Walltime:** max run time (e.g., `04:00:00`)
   - **GPUs:** GPU count, 0 if not needed
3. Click **Save**

### Via the API

```bash
curl -X PUT http://localhost:8000/api/ssh-profiles/{profile_id}/slurm-defaults \
  -H "Content-Type: application/json" \
  -d '{
    "account": "lab_group",
    "partition": "standard",
    "cpus": 4,
    "memory_gb": 16,
    "walltime": "04:00:00",
    "gpus": 0
  }'
```

## Step 6: Configure Remote Paths

Tell AGOUTIC where to stage files on the cluster.

```bash
curl -X PUT http://localhost:8000/api/ssh-profiles/{profile_id}/remote-paths \
  -H "Content-Type: application/json" \
  -d '{
    "remote_input_path": "/data/homezvol0/youruser/agoutic/inputs",
    "remote_work_path": "/data/homezvol0/youruser/agoutic/work",
    "remote_output_path": "/data/homezvol0/youruser/agoutic/outputs"
  }'
```

> **Tip:** Use a scratch or high-performance filesystem for `remote_work_path` if available.

Ensure these directories exist on the cluster:

```bash
ssh youruser@hpc3.rc.uci.edu "mkdir -p ~/agoutic/{inputs,work,outputs}"
```

## Step 7: Submit a Test Job

1. In the AGOUTIC UI, start a new conversation.
2. Tell Cortex: _"Run the hello-world pipeline on HPC3"_
3. Cortex will stage the job with your SLURM defaults.
4. Review and **approve** the staged job.
5. Monitor the stage progression:
   `approved → preparing_inputs → transferring_inputs → queued → running → transferring_outputs → completed`

If the job completes successfully, your HPC3 integration is working.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Connection refused` | SSH service not running or wrong port | Verify host/port; try `ssh -v youruser@host` |
| `Permission denied (publickey)` | Key not authorized on cluster | Re-run `ssh-copy-id`; check `~/.ssh/authorized_keys` on cluster |
| `No such file or directory` (key) | Key file path wrong in SSH profile | Update the key_file_path in profile settings |
| `Host key verification failed` | Cluster host key not in known_hosts | Run `ssh youruser@host` once manually to accept the key |
| `Invalid account` | Wrong SLURM account name | Run `sacctmgr show assoc user=youruser` on cluster to list valid accounts |
| `Invalid partition` | Partition doesn't exist or not allowed | Run `sinfo` on cluster to list available partitions |
| `Resource limit exceeded` | Requested more than partition allows | Reduce CPUs/memory/walltime or use a different partition |
| `sbatch: error: Batch job submission failed` | Generic submission error | Check the full error message; usually account/partition/resource mismatch |
| Job stuck in `queued` | Cluster is busy or resources unavailable | Check `squeue -u youruser` on cluster; wait or adjust resources |
| Transfer fails with `rsync not found` | rsync not installed on cluster | Ask cluster admin to install rsync, or AGOUTIC will fall back to SFTP |
