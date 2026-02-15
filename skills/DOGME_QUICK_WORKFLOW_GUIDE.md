# Dogme Quick Workflow Guide - Shared Instructions

This document contains the consolidated workflow steps for parsing files across all Dogme analysis skills (DNA, RNA, cDNA). Each mode-specific skill references this guide.

## UUID Guidance - Critical Foundation

⚠️ **🚨 CRITICAL - DO THIS FIRST: Validate and Verify the UUID 🚨**

**The #1 cause of "File not found" errors is using the WRONG or CORRUPTED UUID.**

**STEP 1: VALIDATE UUID FORMAT FIRST - BEFORE ANY TOOL CALLS**

⚠️ **UUID corruption happens**: LLMs commonly corrupt long strings including UUIDs
- Pattern: Valid UUID = 36 characters in format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- Each segment must be lowercase hex (0-9, a-f)
- Common errors: Character scrambling (`a2c7` → `a274`), truncation, or case changes

**Validation checklist BEFORE using UUID:**
- ✅ Length is exactly 36 characters
- ✅ Format matches: `8-4-4-4-12` segments separated by dashes
- ✅ All characters are lowercase hex (0-9, a-f)
- ✅ No spaces or extra characters

**Example of corruption:**
```
Original from user: b954620b-a2c7-4474-9249-f31d8d55856f (CORRECT - 36 chars, all hex)
What tool might receive: b954620b-a274-4474-9249-f31d8d55856f (WRONG - a2c7→a274)

✅ CORRECT pattern: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (8-4-4-4-12)
❌ WRONG: A2C7-a2c7 (mixed case)
❌ WRONG: b954620ba2c74474 (missing dashes)
❌ WRONG: b954620b-a27-4474-9249-f31d8d55856f (wrong segment length)
```

**If you see "Job not found" error:**
1. Check the UUID in the error message
2. Compare character-by-character to original user input
3. If they differ (especially in the middle segments), UUID was corrupted
4. Re-read the original user input and use THAT UUID

**STEP 2: Extract UUID from current conversation**

Before making ANY tool calls, look at the conversation for the MOST RECENT "use UUID:" message or "Analysis Ready" message. Extract the UUID from that message.

**WRONG:** Using a UUID from an earlier section of the conversation
- ❌ `6a8613d4-832c-4420-927e-6265b614c8b2` (from analysis of a different job earlier)
- ❌ An old text from history

**RIGHT:** Using the CURRENT, MOST RECENT job UUID
- ✅ `b954620b-a2c7-4474-9249-f31d8d55856f` (from the latest message in THIS conversation)

**Example - What to look for:**
```
Most recent message in conversation:
———————————————
use UUID: b954620b-a2c7-4474-9249-f31d8d55856f     ← COPY THIS EXACTLY, ALL 36 CHARS

Or from analysis:
📊 Analysis Ready: sample_name
Run UUID: b954620b-a2c7-4474-9249-f31d8d55856f     ← THIS IS WHAT YOU USE

Use this UUID in EVERY tool call:
[[DATA_CALL: service=server4, tool=find_file, run_uuid=b954620b-a2c7-4474-9249-f31d8d55856f, file_name=...]]
[[DATA_CALL: service=server4, tool=parse_..., run_uuid=b954620b-a2c7-4474-9249-f31d8d55856f, file_path=...]]
———————————————
```

**Rule:** If you see multiple "use UUID:" messages in conversation history, use ONLY the last one. The most recent one is the active job.

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

**The user provided a filename but analyze_job_results doesn't forward it to your skill.** You must retrieve it yourself:

1. **Look back at conversation history** for what filename the user requested
   - Search for: "parse {filename}" in recent messages
   - Example: "parse jamshid.mm39_final_stats.csv"
   
2. **Extract the exact filename** the user mentioned
   - `jamshid.mm39_final_stats.csv` or
   - `gene_counts` or
   - `mapping_stats`

3. **Use that filename in find_file call**
   ```
   [[DATA_CALL: service=server4, tool=find_file, run_uuid={uuid}, file_name=jamshid.mm39_final_stats.csv]]
   ```

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

Replace `{filename}` with the actual file being searched:
```
[[DATA_CALL: service=server4, tool=find_file, run_uuid={uuid}, file_name={filename}]]
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

Replace `{tool}` with `parse_csv_file`, `parse_bed_file`, or `read_file_content`:
```
[[DATA_CALL: service=server4, tool={tool}, run_uuid={uuid}, file_path=directory/filename.ext]]
```

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
- System looks for: `/media/backup_disk/agoutic_root/server3_work/{uuid}/filename.ext` ❌
- Result: **FILE NOT FOUND ERROR** (file isn't in root, it's in subdirectory)

**If you use:** `file_path=directory/filename.ext`
- System looks for: `/media/backup_disk/agoutic_root/server3_work/{uuid}/directory/filename.ext` ✅
- Result: **SUCCESS** (file found and parsed)

**Key:** The directory prefix (subdirectory) IS REQUIRED and CANNOT be omitted.

---

## Critical Rules - Apply to All Modes

**Before calling any parse tool, verify:**
- ✅ UUID is from MOST RECENT "Analysis Ready" message
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

---

## Reference: Mode-Specific Tools and Directories

Each Dogme skill documents the specific files and tools for its mode:

- **Dogme_cDNA**: Uses `parse_csv_file` for gene counts, `bedMethyl/` and `annot/` directories
- **Dogme_DNA**: Uses `parse_bed_file` for modification calls, `bedMethyl/` directories
- **Dogme_RNA**: Uses `parse_csv_file` and `read_file_content`, `modkit/` and `counts/` directories

See the mode-specific skill file for which files to search for and how to interpret the results.
