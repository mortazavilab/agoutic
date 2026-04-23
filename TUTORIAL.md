# AGOUTIC Tutorial: SSH Profiles, Remote SLURM Runs, Reconcile, and Result Sync

This tutorial walks through the full user flow for running AGOUTIC on a SLURM HPC cluster:

1. Create and authorize an SSH key for the cluster.
2. Create and test an SSH profile.
3. Stage local data or point AGOUTIC at data that already exists on the cluster.
4. Launch Dogme workflows on SLURM.
5. Sync results back to the local AGOUTIC host.
6. Run `reconcile_bams` across completed workflows.

For API-level setup details, see [`docs/cluster_slurm_setup.md`](docs/cluster_slurm_setup.md). For execution-mode reference behavior, see [`docs/user_guide_execution_modes.md`](docs/user_guide_execution_modes.md).

## Before You Start

You should already have:

- a working AGOUTIC deployment
- a cluster account that can log in over SSH
- a writable remote base path on the cluster, usually under scratch or project storage
- Nextflow plus either `apptainer` or `singularity` available on the cluster
- input data either on the AGOUTIC host or already present on the cluster

If your deployment uses `key_file + local_username`, AGOUTIC will ask you to unlock the profile with your local Unix password before broker-backed SSH or rsync operations can proceed.

If you already have a working SSH key and can run `ssh youruser@login.cluster.example.edu` successfully from the AGOUTIC host, you can reuse that key and move to Step 2.

## Step 1: Build and Authorize an SSH Key

If you plan to use `key_file` authentication, start here. If you plan to use `ssh_agent` and already have a working key loaded into your agent, you can skip to Step 2.

### 1. Create a local `.ssh` directory if you do not already have one

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
```

### 2. Generate a new key pair

```bash
ssh-keygen -t ed25519 -f ~/.ssh/agoutic_localcluster -C "agoutic@localCluster"
```

What this creates:

- `~/.ssh/agoutic_localcluster`: your private key
- `~/.ssh/agoutic_localcluster.pub`: your public key

When `ssh-keygen` prompts for a passphrase, using one is recommended. AGOUTIC never stores that passphrase.

### 3. Lock down key permissions

```bash
chmod 600 ~/.ssh/agoutic_localcluster
chmod 644 ~/.ssh/agoutic_localcluster.pub
```

### 4. Copy the public key to the cluster account

If `ssh-copy-id` is available on your machine:

```bash
ssh-copy-id -i ~/.ssh/agoutic_localcluster.pub youruser@login.cluster.example.edu
```

If `ssh-copy-id` is not installed, use this manual fallback instead:

```bash
cat ~/.ssh/agoutic_localcluster.pub | ssh youruser@login.cluster.example.edu 'umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys'
```

Replace `youruser@login.cluster.example.edu` with your real cluster username and login host.

### 5. Test the key before adding it to AGOUTIC

```bash
ssh -i ~/.ssh/agoutic_localcluster youruser@login.cluster.example.edu "hostname"
```

On the first connection, SSH may ask whether you trust the remote host key. Type `yes` to continue.

You are ready for Step 2 when:

- the `hostname` command returns the cluster login node name
- SSH no longer asks for your cluster account password
- you are using the same key path you plan to save in AGOUTIC

## Step 2: Create an SSH Profile

In the UI, open **Settings → SSH Profiles** and create a profile with:

- **Nickname:** a short name such as `localCluster`
- **SSH Host / Port / Username:** the cluster login node and your cluster account
- **Auth Method:** `key_file` or `ssh_agent`
- **Key File Path:** required for `key_file`, for example `~/.ssh/agoutic_localcluster`
- **Local Username:** required for brokered shared-user deployments
- **Remote Base Path:** the root AGOUTIC should manage remotely, for example `/scratch/youruser/agoutic`
- **Default SLURM Account / Partition:** optional but recommended so AGOUTIC does not have to ask every run

If you use a brokered profile, click **Unlock** and enter your local Unix password. Then click **Test Connection**.

You are ready to continue when the profile test succeeds and AGOUTIC can browse the remote base path.

## Step 3: Verify Remote Browsing

Start a chat and confirm that the saved profile works:

- _"list files on localCluster"_
- _"list files in data on localCluster"_

These prompts validate that AGOUTIC can resolve the saved SSH profile and browse under the configured `remote_base_path`.

## Step 4: Choose How You Want to Provide Input Data

AGOUTIC supports two common remote-input paths.

### Option A: Stage Local Data to the Cluster

Use this when your sample data is on the AGOUTIC host and needs to be copied to the cluster first.

Typical prompts:

- _"Run DNA sample Tumor01 from /data/Tumor01 on localCluster"_
- _"Run cDNA sample SampleA from /mnt/reads/SampleA on localCluster with result destination both"_

What AGOUTIC does:

- validates the SSH profile and remote path setup
- stages sample inputs into `{remote_base_path}/data`
- stages or reuses reference assets under `{remote_base_path}/ref`
- prepares a remote workflow directory under `{remote_base_path}/{project_slug}/workflowN`
- shows an approval gate before the actual SLURM submit

### Option B: Use Data That Already Exists on the Cluster

Use this when the input data is already on the HPC filesystem.

Typical prompts:

- _"Run DNA sample Tumor01 on localCluster using remote data at /scratch/youruser/pod5/Tumor01"_
- _"Run RNA sample SampleB on localCluster using remote data at /project/lab/reads/SampleB with both result destinations"_

What changes:

- AGOUTIC skips the local-to-remote upload for the sample input
- the selected remote path is treated as the workflow input source
- reference assets and workflow directories are still prepared under the saved `remote_base_path`

### Optional: Stage Only Without Submitting a Workflow

If you want to prepare the remote input and stop before submission, use a stage-only prompt such as:

- _"stage sample Tumor01 to localCluster"_

This is useful when you want to validate transfer performance, inspect remote paths, or separate upload from later workflow submission.

## Step 5: Submit a Dogme Workflow on SLURM

Once the input source is clear, ask AGOUTIC to run the workflow. Examples:

- _"Run DNA sample Tumor01 on localCluster with 16 CPUs, 64 GB memory, and result destination both"_
- _"Run RNA sample SampleB on localCluster using the gpu partition"_
- _"Run cDNA sample SampleC on localCluster and keep results local"_

During approval, verify:

- the execution mode is **Remote SLURM**
- the correct SSH profile is selected
- the remote input path is correct
- account, partition, CPU, memory, walltime, and GPU choices match your cluster policy
- the **result destination** is what you want

### Choosing the Right Result Destination

- **Local only**: AGOUTIC syncs results back and removes the remote copy afterward
- **Remote only**: results stay on the cluster until you manually sync them later
- **Both**: results stay on the cluster and are also copied back locally

If you plan to analyze outputs immediately in AGOUTIC or run `reconcile_bams`, choose **Local only** or **Both** unless you explicitly want to defer copy-back.

## Step 6: Monitor Staging and Job Execution

AGOUTIC distinguishes between staging tasks and execution jobs.

Common stages you will see:

- `validating_connection`
- `preparing_remote_dirs`
- `transferring_inputs`
- `submitting_job`
- `queued`
- `running`
- `collecting_outputs`
- `syncing_results`
- `completed`

If a transfer stalls, use the staging controls in the UI to refresh, cancel, or resume. If the scheduler is slow, the job may remain in `queued` for some time before transitioning to `running`.

## Step 7: Sync Results Back to the AGOUTIC Host

### Automatic Sync

If the run used **Local only** or **Both**, AGOUTIC automatically copies back results after the remote workflow finishes.

### Manual Sync

If the run used **Remote only**, or if you need to retry copy-back, ask AGOUTIC to sync the results explicitly.

Typical prompts:

- _"sync results back to local for workflow2"_
- _"retry sync results for workflow2"_
- _"sync results for 12345678-1234-1234-1234-123456789abc"_

AGOUTIC resolves the run from the active workflow context, the named `workflowN`, or the run UUID you provide. When the sync starts successfully, progress appears in the task dock and the job status updates.

Use manual sync before downstream analysis if the job was left as **Remote only**.

## Step 8: Analyze or Inspect the Synced Workflow Outputs

Once results are local-accessible, you can inspect them directly in chat.

Useful prompts:

- _"list workflows"_
- _"list files in workflow2/annot"_
- _"summarize QC for workflow2"_
- _"parse workflow2/annot/final_stats.csv"_

This is also the point where remote SLURM runs become eligible for downstream local analysis, differential expression, enrichment, and reconcile workflows.

## Step 9: Run Reconcile BAMs Across Completed Workflows

Use reconcile when you have multiple completed workflows with compatible annotated BAM outputs.

Recommended prerequisites:

- each source workflow contains `*.annotated.bam` outputs
- all source workflows use the same reference genome
- all source workflows use the same annotation GTF, or AGOUTIC can resolve one shared GTF from the workflow configs
- the workflow outputs are local-accessible on the AGOUTIC host

Typical prompts:

- _"Reconcile annotated BAMs from workflow1 and workflow2"_
- _"Run reconcile bams for workflow1 and workflow2 with output prefix merged_tumor"_
- _"Reconcile workflow2 and workflow3 using 16 threads"_

What AGOUTIC does:

- locates candidate annotated BAMs from the named workflows
- runs a preflight validation step before execution approval
- checks reference and annotation compatibility
- asks for approval before reconcile starts
- stages input symlinks under a standard `workflowN/input` directory
- writes reconcile outputs into a new `workflowN` directory for the project

If AGOUTIC reports that references or annotation sources are mixed, stop there and fix the source workflows first rather than forcing a merge.

## Step 10: Work With the Reconcile Outputs

After reconcile completes, treat the reconcile output like any other workflow output.

Useful follow-up prompts:

- _"list files in workflow4"_
- _"summarize QC for workflow4"_
- _"parse workflow4/annot/reconciled_abundance.tsv"_
- _"run differential expression from the reconciled workflow"_

AGOUTIC prefers workflow-local `reconciled.gtf` when present so downstream annotation and plotting stay aligned with the reconciled output.

## Troubleshooting Short List

### The SSH profile will not test successfully

- recheck host, username, key path, and port
- verify the public key really exists on the cluster in `~/.ssh/authorized_keys`
- test the same key manually with `ssh -i ~/.ssh/agoutic_localcluster youruser@login.cluster.example.edu "hostname"`
- if you use `key_file + local_username`, unlock the profile again
- verify the remote account can create directories under `remote_base_path`

### Staging works but submission fails

- confirm the SLURM account and partition are valid for your cluster account
- reduce resource requests if the cluster rejects oversized jobs
- inspect job logs and queue state if the job remains pending

### Reconcile will not approve

- confirm all selected workflows really produced annotated BAMs
- confirm the workflows share one reference genome
- confirm AGOUTIC can resolve one shared annotation GTF
- sync remote-only results back locally before attempting reconcile

### Results are remote-only and analysis does not work

Run a manual sync first, then retry the analysis or reconcile request.

## Recommended First End-to-End Trial

If you are setting this up for the first time, use this sequence:

1. Create the SSH key, copy the public key to the cluster, and verify `ssh -i ~/.ssh/agoutic_localcluster youruser@login.cluster.example.edu "hostname"` works.
2. Create and unlock the SSH profile.
3. Test remote browsing with `list files on localCluster`.
4. Submit one small Dogme run with result destination **Both**.
5. Wait for the automatic sync to complete.
6. Run `list workflows` and summarize the finished workflow.
7. After you have at least two compatible workflows, run reconcile across them.

That path exercises SSH, staging, SLURM submit, copy-back, workflow discovery, and reconcile without requiring manual recovery steps on the first try.
