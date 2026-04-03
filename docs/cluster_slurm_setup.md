# Remote SLURM Cluster Setup Guide

## Prerequisites

- SSH access to your target SLURM cluster
- A valid SLURM account or allocation on that cluster
- Nextflow installed and available in your `$PATH` on the cluster
- AGOUTIC running locally with a user account

For many clusters, Java and container runtime commands are exposed through
environment modules. Ensure compute nodes provide either:

- `singularity`, or
- `apptainer` (with singularity compatibility)

In the examples below, the saved SSH profile nickname is `localCluster`.
Replace the sample hostnames, usernames, accounts, and filesystem paths with
values from your own environment.

## Step 1: Create an SSH Key Pair

Skip this step if you plan to use `ssh_agent` authentication.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/agoutic_localcluster -C "agoutic@localCluster"

chmod 600 ~/.ssh/agoutic_localcluster
chmod 644 ~/.ssh/agoutic_localcluster.pub
```

## Step 2: Authorize the Key on the Cluster

```bash
ssh-copy-id -i ~/.ssh/agoutic_localcluster.pub youruser@login.cluster.example.edu
```

Verify you can connect without a password prompt:

```bash
ssh -i ~/.ssh/agoutic_localcluster youruser@login.cluster.example.edu "hostname"
```

## Step 3: Create an SSH Profile in AGOUTIC

### Via the UI

1. Navigate to **Settings -> SSH Profiles**.
2. Click **Add Profile**.
3. Fill in:
   - **Nickname:** `localCluster`
   - **Host:** `login.cluster.example.edu`
   - **Port:** `22`
   - **Username:** your cluster username
   - **Auth Method:** `key_file` or `ssh_agent`
   - **Key File Path** when using `key_file`: `/path/to/agoutic_localcluster`
   - **Local Username** when using a brokered shared-user deployment: your local Unix account
   - **Remote Base Path:** `/scratch/youruser/agoutic`
4. Save the profile.

### Via the Launchpad API

The Launchpad service exposes SSH profile APIs directly. Replace
`<launchpad-url>` with your Launchpad base URL.

```bash
curl -X POST "<launchpad-url>/ssh-profiles" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-123",
    "nickname": "localCluster",
    "ssh_host": "login.cluster.example.edu",
    "ssh_port": 22,
    "ssh_username": "youruser",
    "auth_method": "key_file",
    "key_file_path": "/home/youruser/.ssh/agoutic_localcluster",
    "local_username": "your-local-unix-user",
    "default_slurm_account": "lab_group",
    "default_slurm_partition": "standard",
    "remote_base_path": "/scratch/youruser/agoutic"
  }'
```

## Step 4: Unlock Brokered Profiles When Needed

If the profile uses `key_file` together with `local_username`, AGOUTIC expects
an active local auth session before staging, remote browsing, or broker-backed
SSH tests can proceed.

### Via the UI

Use the **Unlock** action next to the profile and enter your local Unix
password. AGOUTIC starts a local broker under your Unix account and reuses that
session for later SSH and rsync operations.

### Via the Launchpad API

```bash
curl -X POST "<launchpad-url>/ssh-profiles/<profile-id>/auth-session?user_id=user-123" \
  -H "Content-Type: application/json" \
  -d '{"local_password": "your-local-unix-password"}'
```

You can check whether the session is active:

```bash
curl "<launchpad-url>/ssh-profiles/<profile-id>/auth-session?user_id=user-123"
```

## Step 5: Test the Connection

### Via the UI

Click **Test Connection** next to the SSH profile. A successful test returns
the remote hostname and user identity.

### Via the Launchpad API

```bash
curl -X POST "<launchpad-url>/ssh-profiles/<profile-id>/test?user_id=user-123" \
  -H "Content-Type: application/json" \
  -d '{"local_password": "your-local-unix-password"}'
```

Expected response shape:

```json
{
  "ok": true,
  "message": "SSH connection successful",
  "hostname": "login.cluster.example.edu",
  "remote_user": "youruser",
  "session_started": true
}
```

## Step 6: Configure SLURM Defaults

Set default resource parameters so you do not need to restate them for every
remote run.

### Via the UI

1. Navigate to **Settings -> SSH Profiles -> localCluster**.
2. Configure the default SLURM values that match your cluster policy:
   - **Account:** for example `lab_group`
   - **Partition:** for example `standard` or `gpu`
   - **GPU Account:** only if your cluster separates GPU billing
   - **GPU Partition:** only if GPU jobs must target a separate partition
3. Save the profile.

### Via the Launchpad API

```bash
curl -X PUT "<launchpad-url>/ssh-profiles/<profile-id>?user_id=user-123" \
  -H "Content-Type: application/json" \
  -d '{
    "default_slurm_account": "lab_group",
    "default_slurm_partition": "standard",
    "default_slurm_gpu_account": "lab_group_gpu",
    "default_slurm_gpu_partition": "gpu"
  }'
```

## Step 7: Prepare the Remote Base Path

`remote_base_path` is the canonical root for AGOUTIC-managed remote artifacts.
AGOUTIC derives the remote layout below it:

- `{remote_base_path}/ref` for reference cache entries
- `{remote_base_path}/data` for staged sample-input cache entries
- `{remote_base_path}/{project_slug}/workflowN` for workflow directories

Create the root directory on the cluster if it does not already exist:

```bash
ssh youruser@login.cluster.example.edu "mkdir -p /scratch/youruser/agoutic"
```

Use a filesystem that is appropriate for your cluster's storage policy. If the
cluster distinguishes between home, project, and scratch storage, `scratch` is
usually the right place for `remote_base_path`.

## Step 8: Verify Remote Browsing and Stage-Only Flow

Before submitting a workflow, verify that AGOUTIC can browse and stage against
the saved profile.

### Browse from chat

Use prompts such as:

- `list files on localCluster`
- `list files in data on localCluster`

AGOUTIC resolves relative browse paths under `remote_base_path`.

### Stage remote data without submitting a job

Use a prompt such as:

- `stage sample sampleA to localCluster`

AGOUTIC stages references into `ref/` and input data into `data/`, reusing
cache entries when possible.

## Step 9: Submit a Test Workflow

1. Start a new conversation in AGOUTIC.
2. Tell Cortex to run a small workflow on `localCluster`.
3. Review the generated remote execution plan.
4. Approve the run.
5. Monitor the workflow state transitions through queueing, execution, and
   output transfer.

If that completes successfully, the remote SLURM integration is working.

## Runtime Sanity Check (Recommended)

Run this in the same cluster environment where SLURM jobs execute:

```bash
which java
which singularity || which apptainer
```

If these are missing, load appropriate modules in your cluster environment
before submitting jobs.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Permission denied (publickey)` | Key not authorized on the cluster | Re-run `ssh-copy-id`; check `~/.ssh/authorized_keys` on the cluster |
| `Host key verification failed` | Cluster host key not in `known_hosts` | Run `ssh youruser@host` manually once and accept the host key |
| Unlock fails immediately | Wrong local Unix password or `local_username` mismatch | Confirm the local account name and retry unlock |
| Browse says the profile is locked | Brokered profile has no active auth session | Unlock the profile again before browsing or staging |
| `Invalid account` | Wrong SLURM account name | Ask your cluster admins for the correct account or inspect cluster accounting tools |
| `Invalid partition` | Partition does not exist or is not allowed for your account | Check your cluster's available partitions and update profile defaults |
| Job stays queued | Cluster is busy or resources are constrained | Inspect `squeue -u youruser` on the cluster and adjust requested resources |
| Transfer fails with `rsync not found` | `rsync` is missing on the cluster | Install `rsync` or use a cluster image that includes it |

### Rsync Compression Behavior

For remote SLURM staging and result sync, AGOUTIC keeps rsync compression
enabled but asks rsync to skip CPU-heavy compression for already-compressed
binary formats such as `bam`, `bai`, `cram`, `crai`, `pod5`, `fast5`,
`bigwig`, `parquet`, and similar suffixes.

This improves throughput on common genomics workloads by avoiding wasted
compression work on files that do not shrink meaningfully in transit while
still compressing text-like outputs such as `csv`, `tsv`, `txt`, `html`, and
config files.

If the cluster rsync is too old to support `--skip-compress`, AGOUTIC retries
the transfer once without that option and logs the downgrade. Transfers remain
functional on older clusters, but those runs will use the previous all-files
compression behavior.