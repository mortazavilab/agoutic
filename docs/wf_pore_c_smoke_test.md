# wf-pore-c Smoke Test

This is the Phase 2 manual smoke path only: local wf-pore-c execution, Analyzer recognition, and the automatic summary card. Do not use this guide as a Phase 3 SLURM checklist.

## Inputs

AGOUTIC does not currently ship a canned wf-pore-c demo dataset in-tree. Use a local demo BAM or FASTQ plus reference FASTA that already lives inside the normal user-jail area you use for AGOUTIC runs.

Recommended placeholders:

```bash
export WF_PORE_C_ENABLED=true
export DEMO_SAMPLE=POREC_A
export DEMO_INPUT=/path/in/user-jail/demo/pore-c.concatemers.bam
export DEMO_REF=/path/in/user-jail/demo/reference.fa
export DEMO_OUT=/path/in/user-jail/demo/project-alpha/workflow1
```

Reference preflight is part of the smoke: the FASTA sidecars that your local wf-pore-c setup requires must already exist before launch.

## Start The Stack

```bash
cd /Users/eli/code/agoutic
export WF_PORE_C_ENABLED=true
./agoutic_servers.sh --restart
streamlit run ui/appUI.py --server.address 0.0.0.0 --server.port 8501
```

`agoutic_servers.sh` starts Cortex, Launchpad, and Analyzer. The Streamlit UI still starts separately.

## Step 1: Verify The Preview Command

You can exercise the preview either from Cortex chat or directly against Launchpad REST. A direct preview call is the fastest sanity check because it prints the exact command shape that Phase 2 owns.

```bash
curl -s http://localhost:8003/workflows/preview \
  -H 'Content-Type: application/json' \
  -d @- <<JSON
{
  "workflow_key": "wf_pore_c",
  "sample_name": "${DEMO_SAMPLE}",
  "input_type": "bam",
  "input_path": "${DEMO_INPUT}",
  "reference_fasta": "${DEMO_REF}",
  "output_directory": "${DEMO_OUT}"
}
JSON
```

What to expect in the preview response:

- The command contains `nextflow run epi2me-labs/wf-pore-c -r v1.3.1`.
- The command uses `--bam` for BAM input or `--fastq` for FASTQ input.
- The command contains `--out_dir <.../workflowN>`.
- The command contains `-work-dir <.../.nextflow-work/wf-pore-c/workflowN>`.
- The `-work-dir` path is outside the `workflowN` output folder, not nested under `workflowN/work`.
- The default Phase 2 output flags include `--pairs` and `--mcool`.
- `supports_submission` is `true` when `WF_PORE_C_ENABLED=true`.

If you use Cortex chat instead, ask for a wf-pore-c run with your demo BAM or FASTQ plus FASTA and confirm that the dry-run card shows the same command shape before approving anything.

## Step 2: Submit A Local Run

Recommended manual path: submit from Cortex or the UI so the end-to-end smoke also covers the automatic summary card.

What to expect during submission and launch:

- The job is created with `workflow_key="wf_pore_c"` and `mode=null`.
- Launchpad allocates a normal `workflowN` output directory.
- Launchpad writes wf-pore-c-specific AGOUTIC metadata under `.agoutic/wf-pore-c/`.
- The Nextflow work directory is a sibling scratch path outside the workflow output tree.
- Large BAM or FASTQ inputs are staged symlink-first, with copy fallback only if symlinks are not safe.

If you want a service-level check while the run is active, poll Launchpad directly:

```bash
curl -s http://localhost:8003/jobs/<run_uuid>/status
```

The `<run_uuid>` is visible on the execution card or in the Launchpad submit response.

## Step 3: Verify Workflow Artifacts

After completion, inspect the output `workflowN` directory and confirm these artifacts:

- `wf-pore-c-report.html`
- `pairs/<alias>.pairs.gz`
- `cooler/<alias>.mcool`
- Optional `hi-c/<alias>.hic` only if Hi-C output was requested
- `pairs.stats.txt` when wf-pore-c emitted the metrics file

Also confirm the layout details that are easy to regress:

- `.agoutic.workflow.json` exists and records `workflow_key`, `result_sync_spec`, and `summary_contract`.
- `.agoutic/wf-pore-c/submit-config.json` exists.
- The Nextflow work path is outside the output folder.

## Step 4: Verify Analyzer Summary Fields

Call the Analyzer REST summary endpoint once the run is complete:

```bash
curl -s http://localhost:8004/analysis/summary/<run_uuid>
```

Verify these fields in the summary payload:

- `workflow_key == "wf_pore_c"`
- `mode == null`
- `summary_contract.workflow_version == "v1.3.1"`
- `result_sync_spec.report_filename == "wf-pore-c-report.html"`
- `workflow_summary.sample_alias` matches the sample or sample-sheet alias
- `workflow_summary.metadata.reference_fasta` is populated
- `workflow_summary.metadata.cutter` is populated
- `workflow_summary.artifacts.report_html.present` is correct
- `workflow_summary.artifacts.pairs.present` is correct
- `workflow_summary.artifacts.mcool.present` is correct
- `parsed_reports.pairs_stats.total_pairs` is present when `pairs.stats.txt` was parsed
- `parsed_reports.pairs_stats.cis_trans_ratio` is present when `pairs.stats.txt` was parsed
- `parsed_reports.pairs_stats.duplicate_rate` is present when `pairs.stats.txt` was parsed
- `warnings` is empty for a complete result set, or explicitly lists missing requested outputs or sparse metrics

## Step 5: Verify The Automatic Summary Card

In Cortex, confirm that the completed run produces the wf-pore-c-specific post-run summary rather than a Dogme DNA/RNA/CDNA summary.

What to look for:

- A contact-map-oriented header rather than a Dogme mode summary
- Mention of artifact presence for the report, `pairs.gz`, and `.mcool`
- Mention of parsed `pairs.stats.txt` metrics when available
- Revision, reference, cutter, and sample alias surfaced in the summary
- Warnings shown when requested outputs are missing or metrics are sparse

If the LLM path fails and Cortex falls back to the static summary, it should still stay on the wf-pore-c branch when the caller has enabled that path and should not drift into a Dogme fallback template.