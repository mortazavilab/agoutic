# Skill: Dogme Direct RNA (`run_dogme_rna`)

## Description

This skill handles Direct RNA sequencing data. It performs basecalling, splice-aware alignment using `minimap2` (with high intron tolerance), transcript quantification via `kallisto`, and detection of native RNA modifications (m6A, pseU, Inosine).

**IMPORTANT:** If the user asks for analysis of a COMPLETED job (QC report, results, file analysis), switch to the `analyze_job_results` skill by outputting:

```
[[SKILL_SWITCH_TO: analyze_job_results]]
```

## Detecting Analysis Requests

If the user's message contains any of these patterns, switch to `analyze_job_results`:
- "qc report" / "quality control" / "quality check"
- "analyze results" / "show results" / "check results"
- "what files" / "list files" / "show files"
- "read the output" / "show output"
- "parse" / "view" / "display" (referring to output files)

**Example:**
User: "Can you give me a QC report?"
Agent: "[[SKILL_SWITCH_TO: analyze_job_results]]"

## Inputs

* `query`: (String) The sample identifier or directory path.
* `reference_genome`: (String) Genome key (must have associated GTF for splicing).
* `modifications`: (String, Optional) Default: "inosine_m6A,pseU,m5C".

## Plan Logic

### 1. Discovery & Selection

* **Tool:** `find_pod5_directory(query)`
* **Context:** The agent locates the raw `.pod5` directory corresponding to the sample ID.
* **Output:** Directory path and file verification.

### 2. APPROVAL GATE: Data Validation

* **Condition:** Confirm sample and RNA-specific modification flags.
* **Prompt:** "I found Direct RNA data for '{query}'.
* Modifications to call: '{modifications}'
* Reference: {reference_genome} (GTF required for splicing)


Proceed with analysis setup?"

### 3. Setup (Config Generation)

* **Tool:** `scaffold_dogme_dir(sample_name, input_dir)`
* *Logic:* Sets up the run directory structure.


* **Tool:** `generate_dogme_config(sample_name, read_type="RNA", genome=reference_genome, modifications=modifications)`
* *Logic:* Sets `readType = 'RNA'`. Ensures `kallisto` and `bustools` paths are set for isoform quantification. Configures `annotateRNA` step.



### 4. APPROVAL GATE: Pipeline Launch

* **Condition:** The user must review configuration.
* **Prompt:** "Configuration generated for '{sample_name}'.
* Pipeline: Dogme v1.2.X (RNA Mode)
* Splice Awareness: Active (GTF -> Junction BED auto-conversion)
* Annotation: annotateRNA.py enabled


Ready to launch?"

### 5. Execution & Monitoring

* **Tool:** `submit_dogme_nextflow(sample_name)`
* *Logic:* Submits the job to Server 3.


* **Tool:** `check_nextflow_status(run_uuid)`
* *Logic:* Loops until status is 'COMPLETED' or 'FAILED'.



### 6. Final Reporting

* **Tool:** `get_dogme_report(sample_name)`
* **Output:** Returns JSON summary including `qc_summary.csv` stats, isoform counts, and modification rates.
