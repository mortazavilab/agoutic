# Skill: Reconcile Annotated BAMs (`reconcile_bams`)

## Description

This skill orchestrates reconciliation of annotated BAM outputs across multiple workflows.
It validates that source workflows use the same reference genome before running the
reconcile script.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Reconciling multiple `*.annotated.bam` files
- Cross-workflow BAM merge/reconcile requests
- Preflight reference checks from workflow Nextflow config artifacts
- Approval-gated reconcile execution

### ❌ This Skill Does NOT Handle:
- **Submitting Dogme workflows from raw local inputs** -> `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE search/download requests** -> `[[SKILL_SWITCH_TO: ENCODE_Search]]`
- **General result browsing/QC-only requests** -> `[[SKILL_SWITCH_TO: analyze_job_results]]`

## Input Model

Required before execution approval:
- Source annotated BAM set (auto-discovered or user-provided)
- Shared reference validation from source workflow Nextflow config files
- Output prefix
- Output directory

Optional:
- `min_tpm`
- `min_samples`
- known-only filter toggle

## Plan Logic

1. Locate candidate annotated BAM inputs from workflow outputs.
2. Run `reconcile_bams/check_workflow_references` to inspect workflow Nextflow config files.
3. Block if references are mixed, missing, or ambiguous.
4. Request explicit approval.
5. Run `reconcile_bams/reconcile_bams` after approval.
6. Summarize reconcile outputs.

## Approval Gates

Always require `[[APPROVAL_NEEDED]]` before invoking reconcile execution.
No `RUN_SCRIPT` execution is allowed before approval.

## Important Rules

- Enforce BAM filename convention: `<sample>.<reference>.annotated.bam`.
- Require all selected BAMs to resolve to one reference.
- Surface reference provenance in approval context when available.
