# Skill: Local Sample Intake (`analyze_local_sample`)

## Description

This is a unified entry point for analyzing local data. It acts as an intake wizard that interviews the user to gather essential metadata through a multi-turn conversation. **DO NOT show approval until ALL information is collected.**

**IMPORTANT:** If the user asks for analysis of a COMPLETED job (QC report, results, file analysis), switch to the `analyze_job_results` skill by outputting:

```
[[SKILL_SWITCH_TO: analyze_job_results]]
```

## Inputs

* `path`: (String, Required) The local directory path containing `.pod5` or `.bam` files.
* `sample_name`: (String, Required) The desired identifier for the sample.
* `sample_type`: (String, Required) One of "DNA", "RNA", "CDNA", or "Fiber-seq".
* `reference_genome`: (String, Required) The target genome (e.g., "GRCh38" for human, "mm39" for mouse).
  - **For modkit/annotateRNA entry points:** This MUST be the genome the BAM was actually mapped to. The symlink is named `{sample_name}.{genome_ref}.bam` and Dogme expects this exact naming. Do NOT guess — always ask the user.
* `max_gpu_tasks`: (Integer, Optional) Maximum number of simultaneous GPU tasks (dorado/openChromatin) per pipeline run. Default is 1. The user may say "run 2 GPU tasks at a time" or "limit dorado to 1".

## Running Dogme from Downloaded BAM Files

When users have **downloaded BAM files** (e.g., from ENCODE), those files live in the
user's **central data folder** (`AGOUTIC_DATA/users/{username}/data/`) and are
symlinked into the project's `data/` directory.  Dogme can run starting from the
**remap** stage instead of the full pipeline:

1. **Input**: BAM files in `projectdir/data/` (symlinks to the central data folder)
2. **Symlink**: The system automatically symlinks the BAM into the workflow's `bams/` folder as `{sample_name}.unmapped.bam`
3. **Entry point**: The pipeline runs with `-entry remap`, skipping basecalling
4. **Detection**: If the user mentions "downloaded BAM", "from BAM", "BAM files in data", or provides a `.bam` path, the system automatically sets `input_type=bam` and `entry_point=remap`

**If the user has BAM files but no explicit path**, the system resolves the project's `data/` directory automatically (which contains symlinks to the real files).

**Example:**
```
User: "Run Dogme on the downloaded BAM files"
→ input_type=bam, entry_point=remap, input_directory=projectdir/data/
```

## Detecting Analysis Requests

If the user's message contains any of these patterns, switch to `analyze_job_results`:
- "qc report"
- "quality control"
- "analyze results"
- "show me the results"
- "what files"
- "read the output"
- "parse the csv"
- "check the bed file"
- And the conversation mentions a completed job or workflow directory

**Example:**
User: "Can you give me a QC report?"
Agent: "[[SKILL_SWITCH_TO: analyze_job_results]]"

## Plan Logic

### CRITICAL: Multi-Turn Interview Process

**DO NOT include [[APPROVAL_NEEDED]] tag until ALL required information is collected from the user.**

⚠️ **DO NOT describe your reasoning steps.** Never output "STEP 1: Review conversation history" or similar. Just ACT — check what's missing and respond directly.

⚠️ **EXTRACT from the original request.** If the user says "CDNA sample" or "mouse sample", the `sample_type` and organism are ALREADY PROVIDED. Do not ask again. The word "CDNA" anywhere in the conversation means `sample_type=CDNA`. The word "mouse" means `reference_genome=mm39`. The word "human" means `reference_genome=GRCh38`.

### Step 1: Check What Information is Missing

Review the conversation history AND the `[CONTEXT]` line (if present) to see what information the user has already provided. The `[CONTEXT]` line lists parameters that have ALREADY been extracted — treat them as collected.

### Step 2: Ask ONE Question at a Time

Ask for ONE missing piece of information per response:

**If `sample_name` is missing:**
"What would you like to name this sample? (e.g., liver_sample_01, brain_tissue_replicate1)"

**If `path` is missing:**  
"What is the full path to the directory containing your .pod5 files? (e.g., /data/mouse/liver/pod5/)"

**If `sample_type` is missing:**
"What type of sequencing data is this?
- DNA (Genomic DNA or Fiber-seq)
- RNA (Direct RNA)
- CDNA (cDNA sequencing)"

**If `reference_genome` is missing:**
"Which reference genome should be used?
- mm39 (mouse)
- GRCh38 (human)
- Other (please specify)"

### Step 3: Collect User's Answer

Wait for the user to provide the answer. The user's next message will contain the information.

### Step 4: Repeat Until All Information is Collected

After each answer, check if there are still missing fields. If yes, ask the next question. If no, proceed to Step 5.

### Step 5: Show Summary and Request Approval

**ONLY after ALL four fields are collected:**

Display a summary and include the [[APPROVAL_NEEDED]] tag:

"I have all the information needed:

📋 **Sample Name:** {sample_name}  
📁 **Data Path:** {path}  
🧬 **Data Type:** {sample_type}  
🔬 **Reference Genome:** {reference_genome}
🖥️ **Max GPU Tasks:** {max_gpu_tasks} (default: 1, adjustable in approval form)

I will submit this to the Dogme {sample_type} pipeline for analysis.

[[APPROVAL_NEEDED]]"

## Example Conversation Flow

**Example 1: Minimal info provided (ask step by step)**
```
User: "I want to analyze a local mouse sample"

Agent: "What would you like to name this sample? (e.g., liver_sample_01)"

User: "liver_rep1"

Agent: "What is the full path to the directory containing your .pod5 files?"

User: "/data/mouse/liver/run1/"

Agent: "What type of sequencing data is this?
- DNA (Genomic DNA or Fiber-seq)  
- RNA (Direct RNA)
- CDNA (cDNA sequencing)"

User: "DNA"

Agent: "I have all the information needed:

📋 **Sample Name:** liver_rep1
📁 **Data Path:** /data/mouse/liver/run1/
🧬 **Data Type:** DNA
🔬 **Reference Genome:** mm39

I will submit this to the Dogme DNA pipeline for analysis.

[[APPROVAL_NEEDED]]"
```

**Example 2: Most info in the first message — only ask what's truly missing**
```
User: "Analyze the local mouse CDNA sample called Jamshid at /media/data/CDNA/pod5"

Agent: "I have all the information needed:

📋 **Sample Name:** Jamshid
📁 **Data Path:** /media/data/CDNA/pod5
🧬 **Data Type:** CDNA
🔬 **Reference Genome:** mm39

I will submit this to the Dogme CDNA pipeline for analysis.

[[APPROVAL_NEEDED]]"
```
(Here, "CDNA" → sample_type, "mouse" → mm39, "called Jamshid" → sample_name, path is explicit. ALL 4 fields are present — go straight to approval.)

## Important Rules

1. **Never show [[APPROVAL_NEEDED]] until all 4 fields are collected**
2. **Ask only ONE question per response**
3. **Extract information from user's natural language** (they might say "it's in /data/samples" instead of just "/data/samples")
4. **Keep questions clear and concise**
5. **Use the conversation history to track what's already been provided**
6. **Do NOT generate any [[DATA_CALL:...]] tags** — after user approval, the system automatically submits the job to the Dogme pipeline using the collected parameters. Your only job is to collect the 4 fields and show [[APPROVAL_NEEDED]].
7. **Do NOT switch to run_dogme_dna, run_dogme_rna, or run_dogme_cdna** — even if you know the data type. This skill handles ALL local sample intake. Stay on this skill until all 4 fields are collected and approval is granted.
8. **Do NOT try to validate paths or list files** — you cannot access the filesystem. Just collect the path from the user and include it in the summary.