# Skill: Local Sample Intake (`analyze_local_sample`)

## Description

This is a unified entry point for analyzing local data. It acts as an intake wizard that interviews the user to gather essential metadata through a multi-turn conversation. **DO NOT show approval until ALL information is collected.**

## Inputs

* `path`: (String, Required) The local directory path containing the `.pod5` files.
* `sample_name`: (String, Required) The desired identifier for the sample.
* `sample_type`: (String, Required) One of "DNA", "RNA", "CDNA", or "Fiber-seq".
* `reference_genome`: (String, Required) The target genome (e.g., "GRCh38" for human, "mm39" for mouse).

## Plan Logic

### CRITICAL: Multi-Turn Interview Process

**DO NOT include [[APPROVAL_NEEDED]] tag until ALL required information is collected from the user.**

### Step 1: Check What Information is Missing

Review the conversation history to see what information the user has already provided.

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

I will submit this to the Dogme {sample_type} pipeline for analysis.

[[APPROVAL_NEEDED]]"

## Example Conversation Flow

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

Agent: "Which reference genome should be used?
- mm39 (mouse)
- GRCh38 (human)
- Other (please specify)"

User: "mm39"

Agent: "I have all the information needed:

📋 **Sample Name:** liver_rep1
📁 **Data Path:** /data/mouse/liver/run1/
🧬 **Data Type:** DNA
🔬 **Reference Genome:** mm39

I will submit this to the Dogme DNA pipeline for analysis.

[[APPROVAL_NEEDED]]"
```

## Important Rules

1. **Never show [[APPROVAL_NEEDED]] until all 4 fields are collected**
2. **Ask only ONE question per response**
3. **Extract information from user's natural language** (they might say "it's in /data/samples" instead of just "/data/samples")
4. **Keep questions clear and concise**
5. **Use the conversation history to track what's already been provided**
* **If Type is 'Direct RNA':**
-> Calls `run_dogme_rna(query=path, sample_name=sample_name, reference_genome=reference_genome)`
* **If Type is 'cDNA':**
-> Calls `run_dogme_cdna(query=path, sample_name=sample_name, reference_genome=reference_genome)`



### 5. Final Status

* **Output:** "Handover complete. The {Target_Skill_Name} skill is now initializing analysis for {sample_name}."