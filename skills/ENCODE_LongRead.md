# Skill: ENCODE Long-Read Alignment (Dogme Pipeline)

## Description
This skill handles the end-to-end alignment of ENCODE long-read data (PacBio/Nanopore). It starts from a loose search query (Sample ID or Tissue), manages data retrieval, configures the "Dogme" Nextflow pipeline on Server 3, and returns quality metrics.

## Inputs
- `query`: (String) The search term (e.g., "ENCSR...", "liver tissue").
- `platform`: (String, Optional) "Nanopore" or "PacBio".
- `ref_version`: (String, Optional) "default" (ENCODE standard) or "latest".

## Plan Logic

### 1. Discovery & Selection
- **Tool:** `find_encode_samples(query, platform)`
- **Context:** The agent searches the ENCODE database.
- **Output:** A list of candidate samples with file sizes and file types (fastq/pod5/bam).

### 2. APPROVAL GATE: Data Retrieval
- **Condition:** The user must confirm the correct sample and authorize the storage usage.
- **Prompt:** "I found [N] samples matching '{query}'. The total download size is [Size]. Do you want to proceed with downloading sample '{sample_name}'?"

### 3. Setup (Download & Config)
- **Tool:** `scaffold_dogme_dir(sample_name, file_urls)`
  - *Logic:* Creates directory `{sample_name}/`. Checks if files are local; if not, downloads them.
- **Tool:** `generate_dogme_config(sample_name, ref_version)`
  - *Logic:* Generates the `nextflow.config` file using the specified reference genomes (Default vs Latest).

### 4. APPROVAL GATE: Pipeline Launch
- **Condition:** The user must review the configuration before compute starts.
- **Prompt:** "Configuration generated for '{sample_name}'.
  - Reference: {ref_version}
  - Output Dir: ./{sample_name}/
  - Pipeline: Dogme v2.0 (Server 3)
  
  Ready to launch?"

### 5. Execution & Monitoring
- **Tool:** `submit_dogme_nextflow(sample_name)`
  - *Logic:* Submits job to Server 3. Returns a Run UUID.
- **Tool:** `check_nextflow_status(run_uuid)`
  - *Logic:* Loops until status is 'COMPLETED' or 'FAILED'.

### 6. Final Reporting
- **Tool:** `get_run_metrics(sample_name)`
- **Output:** Returns a JSON summary of the run (N50, Mapping Rate, Read Count).
- **Note:** This skill ends here. Analysis is a separate downstream skill.