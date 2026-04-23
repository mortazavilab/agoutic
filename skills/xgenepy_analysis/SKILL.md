# Skill: XgenePy Analysis (`xgenepy_analysis`)

## Description

This skill runs local XgenePy cis/trans analysis from project-relative count and metadata tables, enforces strict input safety, and returns canonical outputs for downstream parsing.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Running XgenePy analysis with local project data
- Validating required metadata columns (`sample_id`, `strain`, `allele`)
- Producing canonical XgenePy outputs and run manifest
- Parsing XgenePy output artifacts after execution

### ❌ This Skill Does NOT Handle:
- **Remote or SLURM execution**
- **BAM automation or alignment workflows**
- **General Dogme pipeline submission**

## Inputs

- `project_dir`: absolute project directory root used by the service
- `counts_path`: project-relative path to counts CSV/TSV
- `metadata_path`: project-relative path to metadata CSV/TSV
- `output_subdir`: optional project-relative output folder
- `trans_model`: optional trans model (`log_additive` default)
- `fields_to_test`: optional metadata fields for multi-condition analysis
- `combo`: optional condition combo for assignment summarization

## Plan Logic

1. Check for existing XgenePy artifacts.
2. Request/obtain user approval before executing analysis.
3. Run local XgenePy execution tool.
4. Parse canonical outputs for summary.

## Data Calls

```text
[[DATA_CALL: service=xgenepy, tool=run_xgenepy_analysis, project_dir=<project_dir>, counts_path=<counts_rel>, metadata_path=<metadata_rel>, output_subdir=<output_rel>]]
[[DATA_CALL: service=analyzer, tool=parse_xgenepy_outputs, work_dir=<project_dir>, output_dir=<output_rel>]]
```

## Important Rules

1. Use only project-relative path references for `counts_path`, `metadata_path`, and `output_subdir`.
2. Reject absolute or traversal paths.
3. Require approval before execution.
4. Keep execution local-only in Phase 1.
5. Do not invoke SLURM or BAM automation in this skill.
