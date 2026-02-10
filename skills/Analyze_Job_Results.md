# Skill: Analyze Job Results (`analyze_job_results`)

## Description

This skill analyzes completed Dogme pipeline job results. It examines output files (txt, csv, bed) from completed jobs and generates QC reports, summaries, and statistical analyses.

**⚠️ CRITICAL: This skill is for ANALYSIS ONLY. Do NOT submit new jobs. Do NOT call job submission endpoints. Only use the /analysis/* endpoints listed below.**

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

### 2. Job Verification

Once you have the job identifier, use the analysis endpoints to examine existing results.

**CRITICAL: This is a READ-ONLY skill. You will call HTTP GET endpoints to retrieve information. DO NOT create approval gates. DO NOT submit jobs.**

**How to call the analysis endpoints:**

To trigger an analysis API call, include a special tag in your response:

```
[[ANALYSIS_CALL: summary, run_uuid=<uuid>]]
```

Supported analysis calls:
- `[[ANALYSIS_CALL: summary, run_uuid=<uuid>]]` - Get comprehensive QC summary
- `[[ANALYSIS_CALL: categorize_files, run_uuid=<uuid>]]` - Get files grouped by type  
- `[[ANALYSIS_CALL: list_files, run_uuid=<uuid>]]` - List all job files

Example response:
"I'll analyze the recently completed job jamshid (UUID: 4d9376a5-5a4b-4642-86cd-78f7a63fab3d). Let me retrieve the analysis summary...

[[ANALYSIS_CALL: summary, run_uuid=4d9376a5-5a4b-4642-86cd-78f7a63fab3d]]"

The system will automatically call the Server4 endpoint and return the results to continue the conversation.

**Available Analysis Endpoints:**

- `GET http://localhost:8000/analysis/jobs/{run_uuid}/files?extensions=.csv,.txt` - Lists all files for the job
- `GET http://localhost:8000/analysis/jobs/{run_uuid}/files/categorize` - Groups files by type (txt/csv/bed/other)
- `GET http://localhost:8000/analysis/jobs/{run_uuid}/summary` - Comprehensive analysis summary (START HERE)

**Initial verification workflow:**
1. Tell user which job you're analyzing (mention UUID)
2. State you're calling the summary endpoint
3. Wait for the system to return results
4. Parse and present the QC report to the user

### 3. File Discovery & Categorization

Discover what output files are available:

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
