# Skill: Local Sample Intake (`analyze_local_sample`)

## Description

This is a unified entry point for analyzing local data. It acts as an intake wizard that interviews the user to gather essential metadata—Sample Name, Data Type (DNA/RNA/cDNA), and File Location—and then routes the request to the appropriate Dogme pipeline skill.

## Inputs

* `path`: (String, Optional) The local directory path containing the `.pod5` files.
* `sample_name`: (String, Optional) The desired identifier for the sample.
* `sample_type`: (String, Optional) One of "DNA", "RNA", "CDNA", or "Fiber-seq".
* `reference_genome`: (String, Optional) The target genome (e.g., "GRCh38").

## Plan Logic

### 1. Information Gathering (Interview)

* **Tool:** `validate_local_path(path)`
* **Context:** The agent checks if the provided inputs are complete.
* **Logic:**
* If `path` is missing: Ask "Please provide the full path to your local data folder."
* If `sample_name` is missing: Ask "What would you like to name this sample?"
* If `sample_type` is missing: Ask "Is this Direct RNA, cDNA, or Genomic DNA/Fiber-seq data?"
* If `reference_genome` is missing: Ask "Which reference genome should be used (e.g., GRCh38, mm39)?"



### 2. Validation & Inspection

* **Tool:** `scan_directory_contents(path)`
* **Context:** Verifies that the folder actually contains compatible files (e.g., `.pod5` files) and is accessible.
* **Output:** Returns a summary (e.g., "Found 40 pod5 files, 200GB").

### 3. APPROVAL GATE: Confirmation & Routing

* **Condition:** The user must confirm the metadata before the agent selects the specialized pipeline.
* **Prompt:** "I have inspected the data at `{path}`.
* **Sample Name:** {sample_name}
* **Type:** {sample_type}
* **Files:** [Count] pod5 files detected


Based on this, I will route this to the **{Target_Skill_Name}** pipeline. Proceed?"

### 4. Skill Handover (Routing)

* **Tool:** `delegate_to_skill(target_skill, parameters)`
* **Logic:** Based on `{sample_type}`, the agent calls one of the specialized skills:
* **If Type is 'Genomic DNA' or 'Fiber-seq':**
-> Calls `run_dogme_dna(query=path, sample_name=sample_name, reference_genome=reference_genome)`
* **If Type is 'Direct RNA':**
-> Calls `run_dogme_rna(query=path, sample_name=sample_name, reference_genome=reference_genome)`
* **If Type is 'cDNA':**
-> Calls `run_dogme_cdna(query=path, sample_name=sample_name, reference_genome=reference_genome)`



### 5. Final Status

* **Output:** "Handover complete. The {Target_Skill_Name} skill is now initializing analysis for {sample_name}."