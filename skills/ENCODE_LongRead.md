# Skill: ENCODE Long-Read Alignment (Dogme Pipeline)

## Description

This skill handles the retrieval and initial processing of ENCODE long-read data. It searches the ENCODE portal, manages the download of raw `.pod5`/`.fastq` files, and automatically dispatches the analysis to the correct Dogme sub-skill (`DNA`, `RNA`, or `cDNA`) based on the experiment metadata.

**IMPORTANT: If the user asks about LOCAL data (not ENCODE downloads), use [[SKILL_SWITCH_TO: analyze_local_sample]] to switch to the local sample intake skill.**

**IMPORTANT: If the user asks for analysis of COMPLETED jobs (QC report, results, file analysis), switch to the `analyze_job_results` skill:**

```
[[SKILL_SWITCH_TO: analyze_job_results]]
```

## When to Use This Skill

- User mentions "ENCODE", "download from ENCODE", "ENCODE experiment ID", "public data"
- User provides an ENCSR... identifier

## When NOT to Use This Skill

- User mentions "local", "my data", "local sample", "pod5 directory", "local path"
- User asks to analyze existing data on disk
→ In these cases, use: [[SKILL_SWITCH_TO: analyze_local_sample]]

- User asks for "qc report", "quality control", "analyze results", "show results", "what files", "show output"
→ In these cases, use: [[SKILL_SWITCH_TO: analyze_job_results]]

## Inputs

* `query`: (String) The search term (e.g., "ENCSR...", "liver tissue").
* `platform`: (String, Optional) "Nanopore" or "PacBio".
* `ref_version`: (String, Optional) "default" (ENCODE standard) or "latest".

## Plan Logic

### 1. Discovery & Selection

* **Tool:** `find_encode_samples(query, platform)`
* **Context:** The agent searches the ENCODE database.
* **Output:** A list of candidate samples including experiment type (e.g., "polyA plus RNA-seq", "DNA methylation").

### 2. APPROVAL GATE: Data Retrieval

* **Condition:** The user must confirm the correct sample and authorize the storage usage.
* **Prompt:** "I found experiment '{sample_id}' ([Experiment_Type]).
* Description: {description}
* Total download size: [Size]


Do you want to proceed with downloading this dataset?"

### 3. Setup (Download & Staging)

* **Tool:** `download_encode_files(sample_id, output_dir)`
* *Logic:* Downloads the raw pod5/fastq files to the local storage. Verifies checksums.


* **Tool:** `get_experiment_metadata(sample_id)`
* *Logic:* Retrieves specific details to determine the pipeline mode (e.g., is it Direct RNA or cDNA?).



### 4. Classification & Routing

* **Tool:** `delegate_to_skill(target_skill, parameters)`
* **Logic:** Based on the ENCODE experiment type, the agent routes to the specialized skill:
* **Case 'Direct RNA-seq':**
-> Calls `run_dogme_rna(query=local_path, sample_name=sample_id, reference_genome=ref_version)`
* **Case 'Whole Genome Sequencing' or 'Methylation':**
-> Calls `run_dogme_dna(query=local_path, sample_name=sample_id, reference_genome=ref_version)`
* **Case 'polyA RNA-seq' or 'cDNA':**
-> Calls `run_dogme_cdna(query=local_path, sample_name=sample_id, reference_genome=ref_version)`



### 5. Final Status

* **Output:** "Data retrieved for {sample_id}. Analysis delegated to **{Target_Skill_Name}**."
* **Note:** The downstream skill will handle the configuration, execution, and reporting.