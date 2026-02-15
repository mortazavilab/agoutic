# Skill: Analyze Job Results (`analyze_job_results`)

## Description

This skill analyzes completed Dogme pipeline job results. It examines output files (txt, csv, bed) from completed jobs and generates QC reports, summaries, and statistical analyses.

**⚠️ CRITICAL: This skill is for ANALYSIS ONLY. Do NOT submit new jobs. Do NOT call job submission endpoints. Only use the /analysis/* endpoints listed below.**

## Skill Scope & Routing

### ✅ This Skill Handles:
- Retrieving analysis summaries for completed jobs
- Initial job verification and metadata lookup
- **Routing to mode-specific analysis skills** (DNA/RNA/cDNA)
- File discovery and categorization for any completed job
- Initial QC overview before detailed interpretation

**Example questions:**
- "Analyze job results for UUID xyz"
- "Show me what files are available for this job"
- "Give me a QC report"
- "What's the status of recently completed jobs?"

### ❌ This Skill Does NOT Handle:

- **Detailed mode-specific interpretation** → Routes to mode-specific skills
  - DNA results → `[[SKILL_SWITCH_TO: run_dogme_dna]]`
  - RNA results → `[[SKILL_SWITCH_TO: run_dogme_rna]]`
  - cDNA results → `[[SKILL_SWITCH_TO: run_dogme_cdna]]`

- **Submitting new jobs** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
  - "Run analysis on my data"
  - "Submit a new job"
  - "Process these pod5 files"

- **ENCODE data lookup** → `[[SKILL_SWITCH_TO: encode_search]]`
  - "How many files for ENCSR160HKZ?"
  - "Search ENCODE for experiments"
  - "What's available for this biosample?"

- **ENCODE data download/processing** → `[[SKILL_SWITCH_TO: encode_longread]]`
  - "Download and process ENCODE experiment"

### 🔀 General Routing Rules:

**This skill acts as a router:**
1. Verifies job exists and gets mode (DNA/RNA/CDNA)
2. **Immediately switches to the mode-specific skill** for detailed analysis
3. Mode-specific skills handle file parsing, metric interpretation, and results presentation

**If user asks about:**
- **New local data** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE searches** → `[[SKILL_SWITCH_TO: encode_search]]`
- **General help** → `[[SKILL_SWITCH_TO: welcome]]`

**When uncertain:** This is a temporary routing skill. Always switch to a mode-specific skill (DNA/RNA/cDNA) after getting the job summary.

## Inputs

* `run_uuid`: (String) The UUID of the completed job to analyze
* `analysis_type`: (String, Optional) Type of analysis: "qc_report", "summary", "detailed", "files_only"

## Plan Logic

### 1. Information Gathering

**STEP 1: Check Conversation History FIRST**

Before asking the user, scan the recent conversation history for job completion messages:
- Look for "Run UUID: {uuid}" in recent messages (especially from job submission responses)
- Look for sample names mentioned with "completed successfully" or "Job completed"
- The most recent completed job UUID is likely what the user wants analyzed

**STEP 2: If UUID Found in History**
- Use that UUID immediately
- Mention to the user which job you're analyzing: "I'll analyze the recently completed job {sample_name} (UUID: {uuid})"
- Proceed to job verification

**STEP 3: If NO UUID Found in History**
Ask the user for the information:

Example questions:
- "Which job would you like me to analyze? Please provide the job UUID or sample name."
- "What type of analysis would you like? (QC report, summary, or detailed file analysis)"

**DO NOT include [[APPROVAL_NEEDED]] when asking for information - just ask the questions.**

**IMPORTANT:** If you find a UUID in the conversation history, DO NOT submit a new job. Use the EXISTING UUID.

---

**⚠️ CRITICAL: ALWAYS Use the Current UUID from This Conversation**

The analysis tools require the actual job UUID. **Do NOT use old UUIDs from previous analysis sections.**

**RULE:** Use ONLY the UUID from the CURRENT analysis request, which is typically from the most recent job completion message.

**Common mistake:** Analysis from an EARLIER section of this conversation might show UUID `abc123...`. If the user now asks "analyze jamshid", verify there's NOT a MORE RECENT job completion with a DIFFERENT UUID `def456...`. Always use the LATEST one.

**Example:**
```
Earlier message shows:
  Run UUID: abc123...  ← ignore this

Current message shows:
  Run UUID: def456...  ← USE THIS ONE

[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=def456...]]
```

**⚠️ If you get "File not found" error:** Check your UUID against the current job's completion message. Wrong UUID is the most common cause.

## SPECIAL CASE: User Requests Specific File Parsing

**When user says "parse [specific filename]":**

# 🚨🚨🚨 DO NOT DESCRIBE - EXECUTE IMMEDIATELY 🚨🚨🚨
# 
# ❌ DO NOT print: "I will parse the file"
# ❌ DO NOT print: "Let me get the job mode first"
# ❌ DO NOT explain: "I need to determine which Dogme skill to use"
#
# ✅ Execute the [[DATA_CALL:]] IMMEDIATELY below
# ✅ Get the mode from the response
# ✅ Switch to the Dogme skill immediately
# ✅ Let the Dogme skill execute the rest
#
# 🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨

⚠️ **The DataCall below MUST execute right now:**

```
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=b954620b-a2c7-4474-9249-f31d8d55856f]]
```

**After you get the response with `mode` field:**

Switch to the appropriate Dogme skill based on mode:
- If `mode=DNA` or `mode=Fiber-seq`: `[[SKILL_SWITCH_TO: run_dogme_dna]]`
- If `mode=RNA`: `[[SKILL_SWITCH_TO: run_dogme_rna]]`
- If `mode=CDNA`: `[[SKILL_SWITCH_TO: run_dogme_cdna]]`

**Then:** The Dogme skill will execute its Quick Workflow automatically.

**⚠️ CRITICAL RULES:**
- ❌ DO NOT spend time describing "I will parse the file"
- ❌ DO NOT explain the steps first
- ❌ DO NOT print warnings without executing
- ❌ DO NOT call multiple tools — just get mode and switch skill
- ✅ DO call get_analysis_summary immediately
- ✅ DO switch to Dogme skill immediately after
- ✅ Let the Dogme skill handle find_file and parse_csv_file calls

### 2. Job Verification and Mode-Aware Routing

Once you have the job identifier, get the analysis summary first:

```
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=<uuid>]]
```

The summary includes the job's **mode** (DNA, RNA, or CDNA). Based on the mode, switch to the appropriate analysis interpretation skill:

**Mode → Analysis skill:**
- DNA or Fiber-seq → `[[SKILL_SWITCH_TO: run_dogme_dna]]`
- RNA (direct RNA) → `[[SKILL_SWITCH_TO: run_dogme_rna]]`
- CDNA → `[[SKILL_SWITCH_TO: run_dogme_cdna]]`

Each of these skills contains mode-specific guidance on:
- What output files to look for
- How to interpret modification data (DNA/RNA have modifications, cDNA does not)
- What quality thresholds and metrics matter

**Example response:**
"I'll analyze the recently completed job jamshid (UUID: 4d9376a5). Let me retrieve the summary...

[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=4d9376a5-5a4b-4642-86cd-78f7a63fab3d]]"

Then after seeing mode=RNA:
"This is a direct RNA job. Switching to RNA-specific analysis...

[[SKILL_SWITCH_TO: run_dogme_rna]]"

**CRITICAL: This is a READ-ONLY skill. Do NOT create approval gates. Do NOT submit jobs.**

### 3. File Discovery & Categorization

Discover what output files are available:

**⚠️ TIP: Use extension filtering for large jobs**
Since jobs can have 500+ files, use the `extensions` parameter to filter the listing:
```
[[DATA_CALL: service=server4, tool=list_job_files, run_uuid=<uuid>, extensions=.csv,.tsv,.bed]]
```
This returns only the key result files without truncation instead of trying to list all files.

**Endpoint:** `GET /analysis/jobs/{run_uuid}/files/categorize`

This returns files grouped by type:
```json
{
  "txt_files": ["file1.txt", "file2.log"],
  "csv_files": ["stats.csv", "counts.tsv"],
  "bed_files": ["peaks.bed"],
  "other_files": ["report.html"]
}
```

### 4. Generate Analysis Summary

For a comprehensive QC report:

**Endpoint:** `GET /analysis/jobs/{run_uuid}/summary`

This returns:
- File inventory (counts by type)
- Key statistics from CSV files
- BED file summaries (if present)
- Text file previews
- Overall job metrics

### 5. Detailed File Analysis

For specific file analysis:

**Endpoint:** `GET /analysis/files/content?run_uuid={run_uuid}&file_path={path}&preview_lines=50`
- Reads text files with preview

**Endpoint:** `GET /analysis/files/parse/csv?run_uuid={run_uuid}&file_path={path}&max_rows=100`
- Parses CSV/TSV files into structured data
- Returns column names and data rows
- Useful for statistics, counts, metrics

**Endpoint:** `GET /analysis/files/parse/bed?run_uuid={run_uuid}&file_path={path}&max_records=100`
- Parses BED genomic files
- Returns chromosome coordinates and annotations

### 6. QC Report Generation

Generate a comprehensive QC report:

1. **Job Overview**
   - Job UUID
   - Sample name
   - Workflow type
   - Completion status

2. **File Inventory**
   - List all output files by type
   - File sizes and locations

3. **Key Metrics** (from CSV files)
   - Read counts
   - Alignment statistics
   - Quality scores
   - Gene/transcript counts

4. **Quality Assessment**
   - Check for expected output files
   - Validate data completeness
   - Highlight any issues or warnings

5. **Visualizations** (if applicable)
   - Summarize BED regions
   - Distribution statistics
   - Coverage metrics

### 7. Reporting Format

Present results in clear sections:

```markdown
## QC Report: {sample_name}

**Job UUID:** {run_uuid}
**Status:** {status}
**Workflow:** {workflow_type}

### File Summary
- TXT files: {count}
- CSV files: {count}
- BED files: {count}

### Key Metrics
[Present important statistics from CSV files]

### Quality Assessment
[Overall assessment of job quality]

### Detailed Files
[List key output files with descriptions]
```

## Example Usage

**User:** "Give me a QC report for job 4d9376a5-5a4b-4642-86cd-78f7a63fab3d"

**Agent Response:**
1. Verify job exists and is completed
2. Call: `GET /analysis/jobs/4d9376a5-5a4b-4642-86cd-78f7a63fab3d/files/categorize`
3. Call: `GET /analysis/jobs/4d9376a5-5a4b-4642-86cd-78f7a63fab3d/summary`
4. Parse key CSV files for metrics
5. Present comprehensive QC report with file lists, key metrics, and assessment

**User:** "What files are in the jamshid run?"

**Agent Response:**
1. Look up job UUID for "jamshid" sample from recent jobs
2. Call: `GET /analysis/jobs/{run_uuid}/files`
3. Present organized file list grouped by type

**User:** "Show me the alignment statistics"

**Agent Response:**
1. Find alignment stats CSV file from file list
2. Call: `GET /analysis/files/parse/csv?run_uuid={uuid}&file_path=stats.csv`
3. Extract and present key statistics in readable format with explanations

## Available API Endpoints Summary

All analysis endpoints are available on Server 1 at http://localhost:8000:

### File Discovery
- `GET /analysis/jobs/{run_uuid}/files?extensions={optional}` - List files with optional filtering
- `GET /analysis/jobs/{run_uuid}/files/categorize` - Group files by type

### File Reading
- `GET /analysis/files/content?run_uuid={uuid}&file_path={path}&preview_lines={n}` - Read text files
- `GET /analysis/files/parse/csv?run_uuid={uuid}&file_path={path}&max_rows={n}` - Parse CSV/TSV
- `GET /analysis/files/parse/bed?run_uuid={uuid}&file_path={path}&max_records={n}` - Parse BED genomics

### Analysis
- `GET /analysis/jobs/{run_uuid}/summary` - Comprehensive analysis summary

## Notes

- All file paths are relative to the job's work directory
- Files are automatically validated for security
- Large files are truncated with previews
- CSV parsing handles both comma and tab-separated files
- BED parsing supports standard genomic coordinate formats
- Always present results in user-friendly format, not raw JSON
