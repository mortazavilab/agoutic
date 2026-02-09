# Skill: Dogme cDNA (`run_dogme_cdna`)

## Description

This skill processes cDNA long-read data. It focuses on high-accuracy isoform quantification and spliced alignment. Unlike Direct RNA or DNA, **it does not perform modification calling**, making it faster but limited to transcriptomic analysis.

## Inputs

* `query`: (String) The sample identifier or directory path.
* `reference_genome`: (String) Genome key (e.g., "GRCh38").
* `is_single_cell`: (Boolean, Optional) Default: False. (Future proofing for potential single-cell cDNA).

## Plan Logic

### 1. Information Gathering

**CRITICAL: If the user has not provided:**
- Sample name/identifier
- Local path to the data directory

**You MUST ask for this information BEFORE proceeding.** Do NOT guess or hallucinate paths.

Example questions:
- "What is the sample name you'd like to use?"
- "What is the full local path to the pod5 directory? (e.g., `/data/runs/sample1/pod5/`)"

**DO NOT include [[APPROVAL_NEEDED]] when asking for information - just ask the questions.**

### 2. Discovery & Validation

Once you have the path from the user:
* Verify the provided path exists and contains pod5 files
* Confirm the sample name with the user
* Note that modification calling will be skipped for cDNA

### 3. APPROVAL GATE: Data Validation

* **Condition:** Confirm sample and strictly note that modification calling will be skipped.
* **Prompt:** "I found cDNA data for '{query}'.
* Note: Modification calling is DISABLED for cDNA mode.
* Analysis Target: Expression & Splicing only.


Do you want to proceed?"

### 3. Setup (Config Generation)

* **Tool:** `scaffold_dogme_dir(sample_name, input_dir)`
* *Logic:* Sets up the run directory structure.


* **Tool:** `generate_dogme_config(sample_name, read_type="CDNA", genome=reference_genome)`
* *Logic:* Sets `readType = 'CDNA'`. Disables `modkit` steps to save compute. Ensures `annotateRNA.py` runs with the `-CDNA` flag.



### 4. APPROVAL GATE: Pipeline Launch

* **Condition:** Review configuration.
* **Prompt:** "Configuration generated for '{sample_name}'.
* Pipeline: Dogme v1.2.X (cDNA Mode)
* Aligners: minimap2 (spliced) + kallisto
* Mod calling: Skipped


Ready to launch?"

### 5. Execution & Monitoring

* **Tool:** `submit_dogme_nextflow(sample_name)`
* *Logic:* Submits the job to Server 3.


* **Tool:** `check_nextflow_status(run_uuid)`
* *Logic:* Loops until status is 'COMPLETED' or 'FAILED'.



### 6. Final Reporting

* **Tool:** `get_dogme_report(sample_name)`
* **Output:** Returns JSON summary focusing on `inventory_report.tsv` and gene/transcript counts from `reconcileBams.py`.