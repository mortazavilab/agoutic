# Skill: Reconcile Annotated BAMs (`reconcile_bams`)

## Description

This skill orchestrates reconciliation of annotated BAM outputs across multiple workflows.
It validates that source workflows use the same reference genome and annotation GTF
before running the immutable `reconcileBams.py` implementation.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Reconciling multiple `*.annotated.bam` files
- Cross-workflow BAM merge/reconcile requests
- Preflight reference and annotation checks from workflow Nextflow config artifacts
- Approval-gated reconcile execution

### ❌ This Skill Does NOT Handle:
- **Submitting Dogme workflows from raw local inputs** -> `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE search/download requests** -> `[[SKILL_SWITCH_TO: ENCODE_Search]]`
- **General result browsing/QC-only requests** -> `[[SKILL_SWITCH_TO: analyze_job_results]]`

## Input Model

Required before execution approval:
- Source annotated BAM set (auto-discovered or user-provided)
- Shared reference validation from source workflow Nextflow config files
- Annotation GTF used to annotate the source BAMs
- Output prefix
- Output directory

Optional:
- `gene_prefix`
- `tx_prefix`
- `id_tag`
- `gene_tag`
- `threads`
- `exon_merge_distance`
- `min_tpm`
- `min_samples`
- `filter_known`

## Plan Logic

1. Locate candidate annotated BAM inputs from workflow outputs.
2. Run `reconcile_bams/reconcile_bams --preflight-only` to validate that all selected BAMs resolve to one reference and that annotation/GTF inputs are usable.
3. Resolve the exact annotation GTF from workflow config artifacts when possible; otherwise require an explicit annotation path before approval.
4. Block if references or annotation GTFs are mixed, missing, or ambiguous.
5. Request explicit approval and allow the user to edit the final `reconcileBams.py` arguments.
6. Run `reconcile_bams/reconcile_bams`, which stages symlinked inputs into a standard `workflowN` directory and then invokes `reconcile_bams/reconcileBams`.
7. Summarize reconcile outputs from the staged `workflowN` directory. Input
	symlinks remain under `workflowN/input`.

## Approval Gates

Always require `[[APPROVAL_NEEDED]]` before invoking reconcile execution.
No `RUN_SCRIPT` execution is allowed before approval.

## Important Rules

- Enforce BAM filename convention: `<sample>.<reference>.annotated.bam`.
- Require all selected BAMs to resolve to one reference.
- Require one shared annotation GTF, preferring the exact workflow-config value over a generic default.
- Surface reference and annotation provenance in approval context when available.
- Do not modify `reconcileBams.py` as part of this skill.
