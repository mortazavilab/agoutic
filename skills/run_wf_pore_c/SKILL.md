# Skill: Run wf-pore-c (`run_wf_pore_c`)

## Description

This skill collects parameters for Oxford Nanopore EPI2ME `wf-pore-c` and prepares a **dry-run preview only** during Phase 1. Do not imply that execution has started until the workflow submission path exists.

If the user is asking about files or reports from an already completed workflow, switch to `analyze_job_results` instead of staying on this skill.

## Required inputs

Collect these fields before approval:

* Exactly one primary input source:
  * `bam` path, or
  * `fastq` path
* `reference_fasta` path
* `sample_name`

Optional fields:

* `vcf` path
* `sample_sheet` path
* `sample`
* `cutter` (default `NlaIII`)
* Output toggles for `pairs`, `mcool`, `hi_c`, `bed`, `paired_end`, `chromunity`, `coverage`

Default outputs:

* `pairs=true`
* `mcool=true`
* `hi_c=false`
* `bed=false`
* `chromunity=false`
* `coverage=false`
* `paired_end` only when a chosen path explicitly requires it

## Phase 1 behavior

After collecting the required fields, summarize the inputs and request approval for a **dry-run preview**. The system should show the user the planned `nextflow run epi2me-labs/wf-pore-c -r v1.3.1 ...` command shape and validation results, but it must not submit a real job yet.

Do not claim that files were validated or that the command already ran. The backend performs path and sidecar validation after approval.

## Interview rules

1. Ask for one missing required field at a time.
2. Do not emit `[[APPROVAL_NEEDED]]` until the required fields are collected.
3. If the user names `wf-pore-c`, `pore-c`, `mcool`, `pairs`, or `contact map`, stay on this skill unless they are clearly analyzing an existing workflow.
4. Do not switch to Dogme skills.
5. Keep the conversation focused on parameter collection and dry-run preview.