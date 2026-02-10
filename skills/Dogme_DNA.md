# Skill: Dogme DNA / Fiber-Seq (`run_dogme_dna`)

## Description

This skill manages the analysis of Nanopore Genomic DNA (gDNA) or Fiber-seq data using the Dogme pipeline. It handles basecalling, alignment to a reference genome, and the detection of DNA modifications (e.g., 5mC, 6mA) and open chromatin regions using `modkit`.

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

* `query`: (String) The sample identifier or directory path to search for (e.g., "Hela_gDNA_Rep1").
* `reference_genome`: (String) The genome key from the config (e.g., "GRCh38", "mm39").
* `modifications`: (String, Optional) Default: "5mCG_5hmCG,6mA". Specific methylation motifs to call.

## Plan Logic

### 1. Discovery & Selection

* **Tool:** `find_pod5_directory(query)`
* **Context:** The agent locates the raw `.pod5` directory corresponding to the sample ID or path.
* **Output:** A valid directory path and a file count/size summary.

### 2. APPROVAL GATE: Data Validation

* **Condition:** The user must confirm the target dataset and modification settings.
* **Prompt:** "I located the raw data for '{query}' at `[Path]`.
* Total Size: [Size]
* Modifications to call: '{modifications}'
* Mode: Genomic DNA / Open Chromatin


Do you want to proceed?"

### 3. Setup (Config Generation)

* **Tool:** `scaffold_dogme_dir(sample_name, input_dir)`
* *Logic:* Sets up the run directory structure.


* **Tool:** `generate_dogme_config(sample_name, read_type="DNA", genome=reference_genome, modifications=modifications)`
* *Logic:* Generates `nextflow.config`. Crucially sets `readType = 'DNA'` and ensures `modkit` parameters are active for open chromatin calling.



### 4. APPROVAL GATE: Pipeline Launch

* **Condition:** User reviews the config before the expensive GPU job starts.
* **Prompt:** "Configuration generated for '{sample_name}'.
* Pipeline: Dogme v1.2.X (DNA Mode)
* Reference: {reference_genome}
* Modkit: Active (Calling: {modifications})


Ready to launch execution on Server 3?"

### 5. Execution & Monitoring

* **Tool:** `submit_dogme_nextflow(sample_name)`
* *Logic:* Submits the job to Server 3. Returns a Run UUID.


* **Tool:** `check_nextflow_status(run_uuid)`
* *Logic:* Loops until status is 'COMPLETED' or 'FAILED'.



### 6. Final Reporting

* **Tool:** `get_dogme_report(sample_name)`
* **Output:** Returns JSON summary including mapping rates and `modkit` open chromatin bed file locations.