# Troubleshooting Remote Execution

## SSH Connection Failures

### Connection Timeout

```
ssh: connect to host login.cluster.example.edu port 22: Connection timed out
```

**Causes:**
- Host is unreachable (network issue, VPN required)
- Firewall blocking the port

**Fixes:**
1. Verify network connectivity: `ping login.cluster.example.edu`
2. Check if a VPN connection is required
3. Try a different port if configured: `ssh -p 2222 user@host`
4. Contact cluster admins if the host is down

### Authentication Failed

```
Permission denied (publickey,password)
```

**Causes:**
- Key not in `authorized_keys` on the remote host
- Wrong username in SSH profile
- Key file has incorrect permissions

**Fixes:**
1. Re-copy the public key: `ssh-copy-id -i ~/.ssh/agoutic_localcluster.pub user@host`
2. Verify username matches your cluster account
3. Fix key permissions: `chmod 600 ~/.ssh/agoutic_localcluster`
4. Test manually: `ssh -i ~/.ssh/agoutic_localcluster -v user@host`

### Key File Not Found

```
Warning: Identity file /path/to/key not accessible: No such file or directory
```

**Fixes:**
1. Verify the path in your SSH profile settings
2. Ensure the AGOUTIC server process can read the file
3. Update the profile with the correct path

### Host Key Verification Failed

```
Host key verification failed.
```

**Fixes:**
1. Connect manually once to accept the host key: `ssh user@host`
2. Or add the key programmatically: `ssh-keyscan login.cluster.example.edu >> ~/.ssh/known_hosts`

### Unlock or Broker Session Problems

Symptoms include a locked-profile error during remote browsing or staging, or a
connection test that succeeds only when you manually type a password outside AGOUTIC.

**Fixes:**
1. If the profile uses `key_file + local_username`, unlock it again in **Settings → SSH Profiles**
2. Confirm the `local_username` in the profile matches your actual local Unix account
3. Retry the auth session from the API or UI before testing or browsing again
4. If unlock fails immediately, verify the AGOUTIC host can run `su <local_username>` for that account

---

## SLURM Submission Failures

### Invalid Account

```
sbatch: error: invalid account: bad_account
```

**Fixes:**
1. List your valid accounts on the cluster:
   ```bash
   sacctmgr show assoc user=$USER format=Account%30
   ```
2. Update the account in SLURM defaults

### Invalid Partition

```
sbatch: error: invalid partition specified: nonexistent
```

**Fixes:**
1. List available partitions:
   ```bash
   sinfo -s
   ```
2. Check which partitions your account can access:
   ```bash
   sacctmgr show assoc user=$USER format=Partition%30
   ```
3. Update the partition in SLURM defaults

### Resource Limits Exceeded

```
sbatch: error: QOSMaxCpuPerUserLimit
sbatch: error: Batch job submission failed: Job violates accounting/QOS policy
```

**Fixes:**
1. Check partition limits:
   ```bash
   scontrol show partition standard
   ```
2. Reduce requested resources (CPUs, memory, walltime)
3. Try a different partition with higher limits

---

## Job Failures

### Nextflow Aborts Before Any Tasks Start

Symptoms often include:

- `ERROR ~ Execution aborted due to an unexpected error`
- no `.nextflow_success` / `.nextflow_failed` marker files
- workflow stats showing zero submitted/succeeded/failed tasks
- failure around container initialization/pull

**Fixes:**
1. Ensure Java and container runtime are available on compute nodes
   (module-based clusters usually need Java + Singularity/Apptainer).
2. Verify runtime commands in the same cluster environment used by jobs:
   ```bash
   which java
   which singularity || which apptainer
   ```
3. Check focused Nextflow log lines for root cause:
   ```bash
   grep -nEi "error|exception|caused by|singularity|apptainer|denied|timeout|network|no space left" .nextflow.log | tail -n 120
   sed -n '1,220p' .nextflow.log
   ```
4. If your cluster is apptainer-only, use a singularity compatibility shim or
   configure Nextflow container runtime accordingly.

### Out of Memory (OOM)

```
slurmstepd: error: Detected 1 oom_kill event in StepId=12345.batch
```

**Fixes:**
1. Increase `memory_gb` in SLURM defaults or per-job override
2. Check actual memory usage of previous runs:
   ```bash
   sacct -j <job_id> --format=JobID,MaxRSS,ReqMem
   ```
3. Optimize your pipeline's memory usage

### Job Timeout

```
TIMEOUT: Job reached walltime limit
```

**Fixes:**
1. Increase `walltime` in SLURM defaults
2. Check how long similar jobs take:
   ```bash
   sacct -j <job_id> --format=JobID,Elapsed,Timelimit
   ```
3. Consider splitting into smaller jobs

### Node Failure

```
NODE_FAIL: Job terminated due to node failure
```

**Fixes:**
1. Resubmit the job — node failures are transient
2. If persistent, exclude the bad node:
   ```bash
   #SBATCH --exclude=node123
   ```
3. Contact cluster admins if multiple nodes are failing

---

## File Transfer Failures

### rsync Not Found

```
bash: rsync: command not found
```

**Fixes:**
1. Check if rsync is available on the cluster: `which rsync`
2. Load the rsync module if applicable: `module load rsync`
3. AGOUTIC does not currently auto-fallback to SFTP for staging transfers; install `rsync` on the cluster or use an image that includes it
4. Ask cluster admins to install rsync if the cluster environment omits it

### Slow Transfers for BAM or POD5 Outputs

AGOUTIC keeps rsync compression enabled for remote SLURM transfers, but newer
rsync versions can skip expensive compression work for already-compressed
binary suffixes such as `bam`, `bai`, `cram`, `crai`, `pod5`, `fast5`, and
`bigwig`.

If large binary transfers are still slow:
1. Check the cluster rsync version: `rsync --version`
2. Look for an AGOUTIC log warning indicating fallback without `--skip-compress`
3. Upgrade rsync on the cluster if it is too old to support that option
4. Re-test with a representative BAM or POD5 transfer

On older clusters AGOUTIC retries without `--skip-compress` so transfers still
work, but they may use more CPU than newer rsync builds.

### Permission Denied During Transfer

```
rsync: mkstemp failed: Permission denied (13)
```

**Fixes:**
1. Verify you own the target directory on the cluster
2. Check directory permissions: `ls -la /path/to/remote/dir`
3. Ensure `remote_base_path` points to a directory tree you control
4. Create the root if needed: `mkdir -p /scratch/$USER/agoutic`

### Disk Full

```
rsync: write failed: No space left on device (28)
```

**Fixes:**
1. Check disk usage on the cluster:
   ```bash
   df -h /data/homezvol0/$USER
   quota -s
   ```
2. Clean up old job outputs and work directories
3. Use a different filesystem with more space (e.g., scratch)
4. Update `remote_base_path` to point to the larger filesystem

---

## Stage-Specific Issues

### Job Stuck in `awaiting_details`

Cortex is waiting for more information from you.

**Fix:** Provide the missing parameters in the conversation (pipeline name, input files, etc.)

### Job Stuck in `staged`

The job is waiting for your approval.

**Fix:** Review the staged job in the UI and click **Approve** or **Reject**.

### Job Stuck in `transferring_inputs`

File upload to the cluster is stalled or failed.

**Fixes:**
1. Check network connectivity
2. Look at transfer logs in the UI
3. Cancel and resubmit — the transfer will restart

### Browse Fails for a Relative Path

Remote browse prompts such as `list files in data on localCluster` resolve under
the profile's saved `remote_base_path`.

**Fixes:**
1. Confirm the SSH profile has `remote_base_path` set
2. Retry with `list files on localCluster` to confirm the base directory itself is valid
3. If you need an absolute path, specify it explicitly instead of a relative subpath

### Job Stuck in `queued`

The SLURM scheduler hasn't started your job yet.

**Fixes:**
1. Check queue status: `squeue -u $USER`
2. Check estimated start time: `squeue -u $USER --start`
3. This is normal for busy clusters — wait, or reduce resource requests to get scheduled faster

### Job Stuck in `transferring_outputs`

Result download is stalled.

**Fixes:**
1. Check network connectivity
2. Verify the output files exist on the cluster
3. Cancel the transfer and trigger a re-download from the UI

### `transfer_failed` State

A file transfer failed after retries.

**Fixes:**
1. Check the error details in the job's audit log
2. Verify SSH connectivity: test the SSH profile
3. Check disk space on both local and remote systems
4. Manually trigger a retry from the UI

---

## Recovery Procedures

### Resubmit a Failed Job

1. Open the failed job in the UI
2. Click **Resubmit** — this creates a new job with the same parameters
3. Optionally adjust resources before approving

### Adjust Resources After Failure

1. Open the failed job details to see what resources were used
2. Create a new job or tell Cortex: _"Rerun with 32GB memory and 8 CPUs"_
3. Cortex will stage a new job with updated parameters

### Change Partition

If a partition is down or too busy:

1. Tell Cortex: _"Switch to the gpu partition"_
2. Or update SLURM defaults in **Settings → SSH Profiles → SLURM Defaults**
3. Resubmit the job

### Manual Recovery on the Cluster

If AGOUTIC loses track of a job:

```bash
# Check if the job is still running
squeue -u $USER

# Get job details
sacct -j <slurm_job_id> --format=JobID,State,ExitCode,Elapsed

# Manually retrieve results
scp -r user@login.cluster.example.edu:/scratch/$USER/agoutic/<project_slug>/workflowN/ ./local_results/
```
