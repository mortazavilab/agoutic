# wf-pore-c Remote Smoke Test

This is the final Phase 3 manual smoke path: a real wf-pore-c SLURM run against a real cluster with `WF_PORE_C_ENABLED=true`, remote staging, remote execution, remote polling, and manual result copy-back via `/jobs/{run_uuid}/sync-results`.

Use this guide only after the automated closure sweeps are green. The integration is feature-complete; this document is the remaining real-cluster validation path.

## Preconditions

- Cortex, Launchpad, Analyzer, and the Streamlit UI are running from the current repo checkout.
- `WF_PORE_C_ENABLED=true` is exported in the process environment for the stack you are about to test.
- A real SSH profile already exists in Launchpad for the target cluster and the profile can reach `sbatch`, `sacct`, and `nextflow`.
- You have a real local BAM or FASTQ input plus a real reference FASTA inside the normal AGOUTIC user-jail area.
- Any required FASTA sidecars for your cluster run already exist locally before submission.
- The target cluster has enough space under the remote base path for staged inputs, remote outputs, remote work files, and the Apptainer cache.

Critical Phase 3 constraint:

- The Apptainer cache must already be populated on the target cluster. There is no wf-pore-c pre-pull hook. If the cache is empty or missing the required image layers, stop and populate it on the cluster before you submit the smoke run.

Recommended placeholders:

```bash
export WF_PORE_C_ENABLED=true
export USER_ID=user-1
export USERNAME=alice
export PROJECT_ID=proj-remote-demo
export PROJECT_SLUG=proj-remote-demo
export SAMPLE_NAME=POREC_REMOTE_A
export SSH_PROFILE_ID=profile-1
export CLUSTER_HOST=cluster.example.edu
export REMOTE_BASE=/remote/agoutic
export INPUT_TYPE=bam
export INPUT_PATH=/Users/alice/agoutic-data/demo/pore-c.concatemers.bam
export REF_FASTA=/Users/alice/agoutic-data/demo/reference.fa
export OPTIONAL_VCF=
export OPTIONAL_SAMPLE_SHEET=
export CUTTER=DpnII
export SLURM_ACCOUNT=lab
export SLURM_PARTITION=standard
export SLURM_CPUS=16
export SLURM_MEMORY_GB=64
export SLURM_WALLTIME=24:00:00
```

If you are validating FASTQ instead of BAM, set `INPUT_TYPE=fastq` and point `INPUT_PATH` at the real FASTQ path.

## Start The Stack

```bash
cd /Users/eli/code/agoutic
export WF_PORE_C_ENABLED=true
./agoutic_servers.sh --restart
streamlit run ui/appUI.py --server.address 0.0.0.0 --server.port 8501
```

## Step 0: Cluster Preflight

Before submission, verify the remote cluster prerequisites directly over SSH.

```bash
ssh "$CLUSTER_HOST" "command -v nextflow && command -v sbatch && command -v sacct"
ssh "$CLUSTER_HOST" "mkdir -p '$REMOTE_BASE' '$REMOTE_BASE/.nxf-apptainer-cache' && ls -ld '$REMOTE_BASE' '$REMOTE_BASE/.nxf-apptainer-cache'"
ssh "$CLUSTER_HOST" "find '$REMOTE_BASE/.nxf-apptainer-cache' -maxdepth 2 -type f | head"
```

What to verify:

- `nextflow`, `sbatch`, and `sacct` are available on the remote PATH.
- The remote base path is writable.
- `${REMOTE_BASE}/.nxf-apptainer-cache` exists.
- The Apptainer cache is not empty. If `find` prints nothing useful, populate the cache first and do not continue.

## Step 1: Preview The Expected wf-pore-c Command

Use the preview endpoint first. This does not submit a job; it verifies the command family and the wf-pore-c-specific defaults.

```bash
curl -s http://localhost:8003/workflows/preview \
  -H 'Content-Type: application/json' \
  -d @- <<JSON
{
  "workflow_key": "wf_pore_c",
  "sample_name": "${SAMPLE_NAME}",
  "input_type": "${INPUT_TYPE}",
  "input_path": "${INPUT_PATH}",
  "reference_fasta": "${REF_FASTA}",
  "vcf": "${OPTIONAL_VCF}",
  "sample_sheet": "${OPTIONAL_SAMPLE_SHEET}",
  "cutter": "${CUTTER}",
  "output_directory": "${REMOTE_BASE}/${PROJECT_SLUG}/workflow1/output"
}
JSON
```

What to verify:

- The command contains `nextflow run epi2me-labs/wf-pore-c -r v1.3.1`.
- The command uses `--bam` for BAM or `--fastq` for FASTQ.
- The command writes `--out_dir .../workflowN/output`.
- The command includes `-work-dir .../.nextflow-work/wf-pore-c/workflowN`.
- The `-work-dir` path is outside the `workflowN` output tree.
- The preview still shows wf-pore-c defaults such as `--pairs` and `--mcool`.
- `supports_submission` is `true` when `WF_PORE_C_ENABLED=true`.

## Step 2: Submit A Real SLURM Run

Submit through Launchpad with `execution_mode="slurm"`. The example below uses `result_destination="remote"` on purpose so the smoke test explicitly exercises manual copy-back through `/jobs/{run_uuid}/sync-results` after the cluster run completes.

```bash
curl -s http://localhost:8003/jobs/submit \
  -H 'Content-Type: application/json' \
  -d @- <<JSON
{
  "project_id": "${PROJECT_ID}",
  "user_id": "${USER_ID}",
  "username": "${USERNAME}",
  "project_slug": "${PROJECT_SLUG}",
  "sample_name": "${SAMPLE_NAME}",
  "workflow_key": "wf_pore_c",
  "input_type": "${INPUT_TYPE}",
  "input_directory": "${INPUT_PATH}",
  "reference_fasta": "${REF_FASTA}",
  "vcf": "${OPTIONAL_VCF}",
  "sample_sheet": "${OPTIONAL_SAMPLE_SHEET}",
  "cutter": "${CUTTER}",
  "reference_genome": ["GRCh38"],
  "workflow_repo": "epi2me-labs/wf-pore-c",
  "workflow_version": "v1.3.1",
  "execution_mode": "slurm",
  "ssh_profile_id": "${SSH_PROFILE_ID}",
  "slurm_account": "${SLURM_ACCOUNT}",
  "slurm_partition": "${SLURM_PARTITION}",
  "slurm_cpus": ${SLURM_CPUS},
  "slurm_memory_gb": ${SLURM_MEMORY_GB},
  "slurm_walltime": "${SLURM_WALLTIME}",
  "remote_base_path": "${REMOTE_BASE}",
  "result_destination": "remote"
}
JSON
```

Save the returned `run_uuid`.

What to verify:

- The submission returns a real `run_uuid`.
- The response status is `PENDING` or `RUNNING`, not `FAILED`.
- The job was created with `workflow_key="wf_pore_c"` and no Dogme-only mode requirement.
- Launchpad allocates a `workflowN` folder for this run.

## Step 3: Verify Remote Staging And The Remote Command

Phase 3 stages explicit wf-pore-c inputs to the cluster before `sbatch` launch. For a representative run with `remote_base_path=/remote/agoutic` and `project_slug=proj`, the remote layout should match this contract:

- Remote workflow root: `/remote/agoutic/proj/workflowN`
- Remote output directory: `/remote/agoutic/proj/workflowN/output`
- Remote Nextflow work directory: `/remote/agoutic/proj/.nextflow-work/wf-pore-c/workflowN`
- Staged input path: `/remote/agoutic/proj/workflowN/.agoutic/wf-pore-c/staged-inputs/input/...`
- Remote reference cache root: `/remote/agoutic/ref/wf-pore-c/`
- Remote sample-sheet cache root: `/remote/agoutic/data/wf-pore-c/sample-sheet/`
- Remote VCF cache root: `/remote/agoutic/data/wf-pore-c/vcf/`

Use the Launchpad status and detail endpoints while the staging/submission work is happening:

```bash
curl -s "http://localhost:8003/jobs/${RUN_UUID}"
curl -s "http://localhost:8003/jobs/${RUN_UUID}/status"
curl -s "http://localhost:8003/jobs/${RUN_UUID}/logs?limit=200"
```

Then inspect the remote tree on the cluster.

What to verify:

- The BAM or FASTQ was staged remotely under `.agoutic/wf-pore-c/staged-inputs/input/`.
- The reference FASTA and any required sidecars were staged or reused under the wf-pore-c reference cache.
- Optional `sample_sheet` and `vcf` assets were staged into their workflow-specific remote cache roots when provided.
- The generated remote submit config exists under `workflowN/.agoutic/wf-pore-c/remote-submit-config.json`.
- The remote Nextflow command contains `nextflow run epi2me-labs/wf-pore-c -r v1.3.1`.
- The remote command uses `-work-dir ${REMOTE_BASE}/${PROJECT_SLUG}/.nextflow-work/wf-pore-c/workflowN`, not a nested path under `workflowN/output`.
- The submit script exports `NXF_APPTAINER_CACHEDIR=${REMOTE_BASE}/.nxf-apptainer-cache`.
- No `dogme.profile` content appears in the remote command or submit artifacts.

## Step 4: Verify `sbatch` Submission And Poll The Remote Run

Poll Launchpad until the job has a real SLURM job ID and the cluster run reaches a terminal state.

```bash
curl -s "http://localhost:8003/jobs/${RUN_UUID}/status"
```

On the cluster, confirm the same job through SLURM:

```bash
ssh "$CLUSTER_HOST" "sacct -j <slurm_job_id> -o JobID,State,Elapsed,ExitCode"
```

What to verify:

- The status payload eventually includes a non-empty `slurm_job_id`.
- `slurm_state` moves through the expected cluster states and ends at `COMPLETED` for the happy path.
- The Launchpad `run_stage` moves beyond staging into submission and remote execution.
- The cluster-side `sacct` state agrees with Launchpad polling.
- If the run fails, use the Launchpad logs and remote SLURM logs to diagnose the failure before attempting any sync retry.

## Step 5: Copy Results Back With `/sync-results`

After the remote job is complete, trigger explicit copy-back into the local workflow directory.

```bash
curl -s -X POST "http://localhost:8003/jobs/${RUN_UUID}/sync-results"
```

What to verify:

- The sync response reports success.
- The sync response status and transfer state reach `outputs_downloaded`.
- The local workflow directory now contains `wf-pore-c-report.html`.
- The local workflow directory contains the expected wf-pore-c result tree, including the requested Pore-C directories such as `pairs`, `cooler`, and optional `hi-c`.
- `.agoutic.workflow.json` and `.agoutic/wf-pore-c/` metadata are present locally after import.
- The copied-back workflow stays namespaced under `.agoutic/wf-pore-c/` and does not collide with Dogme artifacts.

## Step 6: Partial Or Failed Copy-Back Retry Path

If the first copy-back is partial or fails, fix the underlying issue first, then rerun the supported manual retry path.

Typical causes:

- Remote output tree incomplete because the cluster job is not truly finished yet
- Remote path permissions or quota problems
- Interrupted network transfer
- Partial local workflow contents from an earlier failed sync attempt

Retry command:

```bash
curl -s -X POST "http://localhost:8003/jobs/${RUN_UUID}/sync-results?force=true"
```

What to verify:

- The pre-retry status showed either a failed transfer state such as `transfer_failed` or obviously incomplete local results.
- The root cause was corrected before retry.
- The forced retry ends with `status="outputs_downloaded"`.
- The local workflow tree is complete after retry and no longer reflects the partial failure state.
- The result set includes `wf-pore-c-report.html` plus the requested wf-pore-c output directories, not just a partial subset.

## Final Smoke Checklist

The manual remote smoke is complete when all of the following are true:

- The preview showed `nextflow run epi2me-labs/wf-pore-c -r v1.3.1` with `-work-dir` outside `workflowN/output`.
- The cluster-side Apptainer cache was verified as populated before submission.
- BAM or FASTQ input plus reference assets were staged or reused on the cluster in the wf-pore-c-specific locations.
- A real `sbatch` submission happened and Launchpad polling agreed with `sacct`.
- Remote outputs were copied back successfully through `/jobs/{run_uuid}/sync-results`.
- The manual retry path with `force=true` is understood and ready if the first copy-back is partial or fails.
- The final local workflow tree contains `wf-pore-c-report.html` and the expected wf-pore-c output directories under the normal AGOUTIC workflow layout.