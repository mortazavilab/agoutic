# Dogme Quick Workflow Guide - Shared Instructions

This document contains the consolidated workflow steps for parsing files across all Dogme analysis skills (DNA, RNA, cDNA). Each mode-specific skill references this guide.

## Workflow Directory Guidance - Critical Foundation

⚠️ **🚨 CRITICAL - DO THIS FIRST: Find Your Workflow Directory 🚨**

**The system uses workflow directories (not UUIDs) to locate job files.**

Each completed job has a `work_dir` — the absolute path to its workflow folder (e.g. `/media/backup_disk/agoutic_root/users/alice/my-project/workflow1/`).

**STEP 1: Extract work_dir from context injection**

**PREFERRED: Check for `[CONTEXT: work_dir=...]` at the start of the user message.**
The system automatically injects workflow directory paths. If present, use the `work_dir` value directly.

**Single workflow:**
```
[CONTEXT: work_dir=/media/backup_disk/agoutic_root/users/alice/my-project/workflow1, sample=jamshid]
```

**Multiple workflows:**
```
[CONTEXT: workflows=[
  workflow1 (sample=jamshid, mode=RNA): work_dir=/media/.../workflow1
  workflow2 (sample=ali, mode=DNA): work_dir=/media/.../workflow2
], active_workflow=workflow2]
```

**Use the work_dir value from the CONTEXT line in EVERY tool call:**
```
[[DATA_CALL: service=analyzer, tool=find_file, work_dir=/media/.../workflow1, file_name=gene_counts]]
[[DATA_CALL: service=analyzer, tool=parse_csv_file, work_dir=/media/.../workflow1, file_path=counts/sample_gene_counts.csv]]
```

**FALLBACK:** If no `[CONTEXT: ...]` line, look for the most recent "Analysis Ready" or "Workflow:" message in conversation history.

**Rule:** If you see multiple workflows, match the file to the correct workflow by sample name. Use only the `work_dir` for that workflow.

---

## File Summary Filtering - Exclude Work/Intermediate Files

⚠️ **CRITICAL: When presenting file summaries to users, filter out work/intermediate files**

**The get_analysis_summary tool counts ALL files in the work directory, including intermediate processing files.** For user-facing summaries, exclude work files to show only final results.

**Filtering rules:**
- ✅ **Include final result files:**
  - Files in `annot/` (annotations, final stats)
  - Files in `counts/` (gene/transcript counts)
  - Files in `bedMethyl/` (methylation/modification data)
  - Files in `modkit/` (modification pileup data)
  - Files in `fiberseq/` (chromatin accessibility data)

- ❌ **Exclude work/intermediate files:**
  - Files in `work/` directory (Nextflow intermediate files)
  - Temporary files (*.tmp, *.log during processing)
  - Processing artifacts and cache files

**Example filtered summary presentation:**
```
📊 Final Result Files (excluding work/intermediate files):
- TXT files: 13 (QC reports, summaries, stats)
- CSV files: 12 (gene counts, transcript data, metrics)  
- BED files: 1 (genomic regions, modifications)

Total result files: 26 (vs 502 total including work files)
```

**Why filter:** Work directories can contain hundreds of intermediate files that are not meaningful to users. Focus on final analysis outputs.

---

## Retrieving Filename When Switched by analyze_job_results

⚠️ **CRITICAL: When Dogme skill is activated via analyze_job_results**

If you were switched from `analyze_job_results` with a "parse {filename}" request:

**The system automatically injects job context into your message.** Look for a line like:
```
[CONTEXT: work_dir=/media/backup_disk/agoutic_root/users/alice/project/workflow1, sample=jamshid]
```

**USE THAT work_dir DIRECTLY in your DATA_CALL tags. Do NOT search for it.**

1. **Read the `[CONTEXT: ...]` line** — it has the work_dir you need
2. **Extract the filename** from the user's message (e.g., "parse jamshid.mm39_final_stats.csv")  
3. **Execute find_file IMMEDIATELY** with the ACTUAL path from [CONTEXT], not a template variable:
   ```
   [[DATA_CALL: service=analyzer, tool=find_file, work_dir=/media/.../workflow1, file_name=jamshid.mm39_final_stats.csv]]
   ```
   ⚠️ **NEVER write `work_dir={work_dir}` or `work_dir={work_dir_from_context}` — always use the REAL PATH.**

4. **Continue with STEP 1-5 below** to find and parse the file

**Key difference:** User asked for a SPECIFIC file, not a full analysis. Find and parse ONLY what they requested, then present it. Don't do full analysis unless they ask.

---

## Directory Prefix Requirement - Unmissable Warning

### 🚨 UNMISSABLE WARNING 🚨

**⚡ THE #1 ERROR THAT CAUSES "FILE NOT FOUND" ⚡**

**❌ WRONG:** `file_path=filename.ext` (no directory)
- This WILL fail with error: "File not found"
- System looks in: `/work_dir/filename.ext` (NOT THERE)

**✅ RIGHT:** `file_path=directory/filename.ext` (with directory)
- This will WORK
- System looks in: `/work_dir/directory/filename.ext` (FOUND)

**THE RULE:** Directory prefix (`annot/`, `bedMethyl/`, `counts/`, `modkit/`) is ALWAYS REQUIRED

---

## Step-by-Step Workflow: User Asks to Parse a File

When user says "parse [filename]":

# ⚠️⚠️⚠️ EXECUTE IMMEDIATELY - DO NOT DESCRIBE ⚠️⚠️⚠️
# Do NOT print messages like "I will parse the file"
# Do NOT explain the steps
# Just execute the DataCalls below right now
# ⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️

### STEP 1: EXECUTE NOW - Call find_file

Replace `{filename}` with the actual file being searched.
**⚠️ Replace `{work_dir}` with the ACTUAL path from [CONTEXT] — e.g. `/media/.../workflow1`. NEVER output a template variable.**
```
[[DATA_CALL: service=analyzer, tool=find_file, work_dir=/media/.../workflow1, file_name={filename}]]
```

### STEP 2: Extract `primary_path` from the response

The response will look like this:
```json
{
  "success": true,
  "primary_path": "directory/filename.ext",  ← COPY THIS ENTIRE STRING
  "paths": ["directory/filename.ext"],       ← Only result paths (work/ excluded)
  "file_count": 1
}
```

### STEP 3: Copy the ENTIRE `primary_path` value EXACTLY

Including the directory prefix:
- ✅ DO: `directory/filename.ext` (full path with directory)
- ❌ DO NOT: `filename.ext` (missing directory prefix)
- ❌ DO NOT: Extract partial filenames
- ❌ DO NOT: Modify case

### STEP 4: Use it in parse call - COPY THE EXACT VALUE

Use the correct tool (`parse_csv_file`, `parse_bed_file`, or `read_file_content`) with the REAL work_dir path:
```
[[DATA_CALL: service=analyzer, tool=parse_csv_file, work_dir=/media/.../workflow1, file_path=annot/sample_final_stats.csv]]
```
⚠️ **NEVER use template variables like `{tool}` or `{work_dir}` — always substitute with actual values.**

**Tool selection guide:**
- **CSV files** (gene counts, stats): Use `parse_csv_file`
- **BED files** (genomic regions, modifications): Use `parse_bed_file`
- **Text files** (summaries, logs, flagstat): Use `read_file_content`

### STEP 5: WHEN parse SUCCEEDS - Present data immediately

✅ **When response has `"success": true`:**
- Extract the `data` field (contains parsed rows/content)
- Extract the `columns` field if present (column names)
- Format as readable table or structure
- Present to user immediately
- Include brief interpretation (mode-specific)

❌ **NEVER exhibit these patterns:**
- Say "The query did not return expected data" (it is there in `data` field)
- Say "content is not provided in usable format" (extract and format it)
- Say "results show metadata but actual content..." (extract `data` field immediately)
- Ask for different parameters or tools (success means parsing worked)

**CSV data presentation:**
```
Here's the parsed data from {filename}:

| {Column1} | {Column2} | ... |
|-----------|-----------|-----|
| {val}     | {val}     | ... |
(show all rows from data array)

Summary:
- Total rows: {row_count}
- File: {file_path}
```

**BED/genomic data presentation:**
```
Here's the genomic data from {filename}:

| Chromosome | Start | End | Name | Feature | Value |
|-----------|-------|-----|------|---------|-------|
| {chr}     | {s}   | {e} | {n}  | {feat}  | {val} |
(show all records)

Summary:
- Total features: {feature_count}
- File: {file_path}
```

**Text file content presentation:**
```
Here's the content from {filename}:

{key sections or full content}

Summary:
- File: {file_path}
- Total lines: {line_count}
```

Then add mode-specific interpretation (see individual Dogme skills)

---

## Demonstration: What Happens with Wrong vs Right Paths

**If you use:** `file_path=filename.ext`
- System looks for: `/work_dir/filename.ext` ❌
- Result: **FILE NOT FOUND ERROR** (file isn't in root, it's in subdirectory)

**If you use:** `file_path=directory/filename.ext`
- System looks for: `/work_dir/directory/filename.ext` ✅
- Result: **SUCCESS** (file found and parsed)

**Key:** The directory prefix (subdirectory) IS REQUIRED and CANNOT be omitted.

---

## Critical Rules - Apply to All Modes

**Before calling any parse tool, verify:**
- ✅ work_dir is from the `[CONTEXT: ...]` line or the MOST RECENT "Analysis Ready" message
- ✅ `primary_path` includes a directory prefix
- ✅ Using EXACT copy of `primary_path` from find_file response
- ✅ Not modifying or shortening the path
- ✅ Not changing case
- ✅ Not ignoring the directory prefix

**DO:**
- Execute tool calls immediately — don't explain what you're about to do
- Present results with clear explanations of what they mean
- Ask clarifying questions if user's intent is ambiguous
- Offer suggestions for further analysis based on what you find

**DON'T:**
- Explain your process step-by-step before executing
- Ask permission for obvious next steps
- Get stuck in explanation loops — act first, explain results
- Say "the query did not return expected data" when `success: true`
- Output template variables like `{work_dir}` — always use the actual path

---

## Browsing Commands

The system handles these commands automatically — just respond naturally to the results.

**"list workflows"** — Lists all workflow folders in the project. Useful when user has run multiple jobs.

**"list files"** — Lists files in the current (most recent) workflow.

**"list files in workflow2/annot"** — Lists files in a specific subfolder, optionally prefixed by a workflow name.

**"parse annot/File.csv"** — Parses a file using a relative path within the current workflow. The system resolves the path automatically.

**"parse workflow2/annot/File.csv"** — Parses a file in a specific workflow when multiple workflows exist.

---

## Reference: Mode-Specific Tools and Directories

Each Dogme skill documents the specific files and tools for its mode:

- **Dogme_cDNA**: Uses `parse_csv_file` for gene counts, `bedMethyl/` and `annot/` directories
- **Dogme_DNA**: Uses `parse_bed_file` for modification calls, `bedMethyl/` directories
- **Dogme_RNA**: Uses `parse_csv_file` and `read_file_content`, `modkit/` and `counts/` directories

See the mode-specific skill file for which files to search for and how to interpret the results.
