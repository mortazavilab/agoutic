# Skill: Haplotype With VCF (`haplotype_with_vcf`)

## Description

This skill labels long-read DNA, RNA, or cDNA BAM reads using a VCF.
It resolves BAM inputs from existing workflow outputs, validates the BAM/VCF
contract in a preflight step, auto-compresses or indexes VCF inputs when
needed, requires explicit approval, and then runs a local allowlisted script
that writes haplotyped BAMs, indexed split BAMs, and summary tables.

For recognized mouse founder-panel VCFs, the skill also supports founder-aware
haplotyping with canonical founder labels, F1 shorthand, and pairwise founder
restriction. When a mouse/mm39 founder request omits the VCF, the skill derives
`mgp_REL2021_snps_founders.vcf.gz` from the same directory as the configured
mm39 reference FASTA instead of relying on a hardcoded host path.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Long-read genomic DNA haplotyping with a VCF
- Long-read RNA or cDNA haplotyping with a VCF
- Mouse founder-panel haplotyping with canonical founder names such as `C57BL_6J`, `CAST_EiJ`, and `PWK_PhJ`
- Natural-language requests such as `haplotype RNA workflow7 with file parent.vcf`
- Natural-language mouse founder requests such as `haplotype mouse sample B6 Cast F1 workflow7` or `haplotype mouse between B6 and CAST workflow7`
- Slash-style requests such as `/haplotype DNA workflow7 parent.vcf` and `/haplotype RNA workflow7 --vcf-sample B6,CAST`
- Workflow-aware BAM discovery from Dogme and reconcile outputs

### ❌ This Skill Does NOT Handle:
- Creating or calling a VCF from BAM inputs
- Short-read haplotyping
- Single-cell or single-nucleus workflows
- Remapping or allele-aware realignment
- Generic polyploid or arbitrary non-founder multi-sample panel assignment

## Input Model

Required before execution approval:
- Workflow reference or explicit BAM selection
- Assay mode (`DNA`, `RNA`, or `cDNA`)
- A VCF path (`.vcf` or `.vcf.gz`), unless the request is a mouse/mm39 founder-panel run that should use the default mm39 founder VCF

Optional:
- Explicit BAM names when narrowing a multi-BAM workflow
- Cross-project workflow references such as `otherproject:workflow7`
- Explicit VCF sample or founder selection
- Founder-pair restriction through repeated `--vcf-sample`, comma-separated `--vcf-sample founderA,founderB`, F1 shorthand such as `B6CastF1` or `B6 Cast F1`, or natural-language phrases such as `between B6 and CAST`
- Parent or genotype label overrides for legacy one-sample or two-sample mode
- Output workflow destination

## Founder-Aware Mouse Behavior

- Founder matching is case-insensitive and ignores `/`, `_`, `-`, and spaces.
- `ref`, `B6`, `C57BL6`, and `C57BL6/J` all resolve to the canonical founder label `C57BL_6J`.
- Aliases such as `CAST`, `CAST/J`, and `CAST_EiJ` resolve to the same founder.
- Founder-panel runs default to all recognized founders when no subset is supplied.
- Pairwise founder runs keep `C57BL_6J` as haplotype 1 / label A when the reference founder participates.
- Founder-panel outputs keep canonical founder labels in approvals, BAM tags, summaries, and filenames.

## Plan Logic

1. Locate eligible BAM inputs using workflow-aware discovery rules.
	Cross-project workflow references such as `otherproject:workflow7` resolve against the same owner root as the active project.
2. Resolve founder aliases or VCF sample restrictions when present.
3. Resolve the default mm39 founder VCF for omitted-VCF mouse founder requests.
4. Run a preflight script to validate BAMs, indexes, VCF shape, and assignment mode, auto-preparing a compressed/indexed VCF when possible.
5. Request explicit approval that shows the final BAM set, assay mode, resolved VCF path, selected VCF samples or founders, label mapping, thresholds, and destination workflow.
6. Run the haplotyping script through Launchpad `run_allowlisted_script`.
7. Locate generated BAMs, indexes, and summary files.
8. Parse and summarize the resulting outputs.

## Approval Gates

Always require `[[APPROVAL_NEEDED]]` before invoking haplotyping execution.
No `RUN_SCRIPT` execution is allowed before approval.

## Important Rules

- Regular Dogme RNA or cDNA workflows use annotated BAMs from `annot`.
- Regular Dogme DNA workflows use mapped BAMs from `bams`, excluding unmapped BAMs.
- Reconcile workflow outputs are treated as RNA-only and use annotated BAMs from the workflow root.
- Plain `.vcf` inputs are compressed to `.vcf.gz` and indexed automatically when AGOUTIC can write beside the source file.
- Missing `.tbi` or `.csi` indexes for `.vcf.gz` inputs are built automatically when possible.
- Founder-panel mode supports cleanly distinguishable multiallelic SNPs and writes one split BAM per selected founder plus an ambiguous BAM.
- Output BAMs must be indexed.
- Output filenames must include `haplotyped` for combined BAMs and canonical founder, genotype, or `ambiguous` labels for split BAMs.