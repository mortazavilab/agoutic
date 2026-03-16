# Troubleshooting Remote Execution

## SSH Connection Failures

### Connection Timeout

```
ssh: connect to host hpc3.rc.uci.edu port 22: Connection timed out
```

**Causes:**
- Host is unreachable (network issue, VPN required)
- Firewall blocking the port

**Fixes:**
1. Verify network connectivity: `ping hpc3.rc.uci.edu`
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
1. Re-copy the public key: `ssh-copy-id -i ~/.ssh/agoutic_hpc3.pub user@host`
2. Verify username matches your cluster account
3. Fix key permissions: `chmod 600 ~/.ssh/agoutic_hpc3`
4. Test manually: `ssh -i ~/.ssh/agoutic_hpc3 -v user@host`

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
2. Or add the key programmatically: `ssh-keyscan hpc3.rc.uci.edu >> ~/.ssh/known_hosts`

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
3. AGOUTIC will automatically fall back to SFTP if rsync is unavailable
4. Ask cluster admins to install rsync

### Permission Denied During Transfer

```
rsync: mkstemp failed: Permission denied (13)
```

**Fixes:**
1. Verify you own the target directory on the cluster
2. Check directory permissions: `ls -la /path/to/remote/dir`
3. Ensure the remote path config points to directories you control
4. Create missing directories: `mkdir -p ~/agoutic/{inputs,work,outputs}`

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
4. Update `remote_work_path` to point to the larger filesystem

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
scp -r user@hpc3:~/agoutic/outputs/<job_id>/ ./local_results/
```
