# Skill: Haplotype With VCF (`haplotype_with_vcf`)

## Description

This skill labels long-read DNA or RNA BAM reads using an indexed VCF.
It resolves BAM inputs from existing workflow outputs, validates the BAM/VCF
contract in a preflight step, requires explicit approval, and then runs a
local allowlisted script that writes haplotyped BAMs and summaries.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Long-read genomic DNA haplotyping with a VCF
- Long-read RNA or cDNA haplotyping with a VCF
- Natural-language requests such as `haplotype RNA workflow7 with file parent.vcf`
- Slash-style requests such as `/haplotype DNA workflow7 parent.vcf`
- Workflow-aware BAM discovery from Dogme and reconcile outputs

### ❌ This Skill Does NOT Handle:
- Creating or calling a VCF from BAM inputs
- Short-read haplotyping
- Single-cell or single-nucleus workflows
- Remapping or allele-aware realignment
- Indels, multiallelic sites, or polyploid assignment logic in v1

## Input Model

Required before execution approval:
- Workflow reference or explicit BAM selection
- Indexed VCF path
- Assay mode (`DNA` or `RNA`)

Optional:
- Explicit BAM names when narrowing a multi-BAM workflow
- Explicit VCF sample or sample pair selection
- Parent or genotype label overrides
- Output prefix or output workflow destination

## Plan Logic

1. Locate eligible BAM inputs using workflow-aware discovery rules.
2. Run a preflight script to validate BAMs, indexes, VCF shape, and label mode.
3. Request explicit approval that shows the final BAM set, assay mode, VCF path,
   selected VCF sample or samples, label mapping, thresholds, and destination workflow.
4. Run the haplotyping script through Launchpad `run_allowlisted_script`.
5. Locate generated BAMs, indexes, and summary files.
6. Parse and summarize the resulting outputs.

## Approval Gates

Always require `[[APPROVAL_NEEDED]]` before invoking haplotyping execution.
No `RUN_SCRIPT` execution is allowed before approval.

## Important Rules

- Regular Dogme RNA or cDNA workflows use annotated BAMs from `annot`.
- Regular Dogme DNA workflows use mapped BAMs from `bams`, excluding unmapped BAMs.
- Reconcile workflow outputs are treated as RNA-only and use annotated BAMs from the workflow root.
- Output BAMs must be indexed.
- Output filenames must include `haplotyped` for combined BAMs and genotype or `ambiguous` labels for split BAMs.