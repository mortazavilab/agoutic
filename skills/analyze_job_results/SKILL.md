# Skill: Analyze Job Results (`analyze_job_results`)

## Description

This skill analyzes completed workflow results across Dogme, `reconcile_bams`, `haplotype_with_vcf`, `differential_expression`, and `wf_pore_c`. It examines synced output files (txt, csv/tsv, bed, reports, plots) from completed or otherwise analyzable workflows and routes only when deeper Dogme mode-specific interpretation is actually needed.

If a workflow is marked `FAILED` or `CANCELLED` but its result files have been synced and are present on disk, it is still a valid analysis target. When the user explicitly names that workflow, prefer checking the requested workflow's files first instead of automatically switching to a different completed workflow.

**⚠️ CRITICAL: This skill is analysis-first. Do NOT submit new Dogme, script, or Nextflow jobs. The only allowed execution exception is a local allowlisted utility script that inspects an existing BED file and returns per-chromosome counts. For normal result analysis, only use the /analysis/* endpoints listed below.**

## Skill Scope & Routing

### ✅ This Skill Handles:
- Retrieving analysis summaries for completed jobs
- Initial job verification and metadata lookup
- **Routing Dogme results to mode-specific analysis skills** (DNA/RNA/cDNA) when deeper interpretation is needed
- File discovery and categorization for any completed job
- Initial QC overview before detailed interpretation
- Workflow-family-aware summaries for `reconcile_bams`, `haplotype_with_vcf`, `differential_expression`, and `wf_pore_c`
- Counting BED regions per chromosome via the bundled allowlisted script when the user explicitly asks for chromosome counts from a BED file
- BAM-adjacent result triage using supported Analyzer tools only (`list_job_files`,
  `find_file`, `read_file_content`, `parse_csv_file`, `parse_bed_file`) when
  direct BAM inspection tools are unavailable
- Cross-workflow region comparisons that reference results as `project_slug:workflowN`, especially Venn/UpSet requests over Fiber-seq open-chromatin BED outputs

**Example questions:**
- "Analyze job results for UUID xyz"
- "Show me what files are available for this job"
- "Give me a QC report"
- "What's the status of recently completed jobs?"
- "Make a venn diagram of the regions in testslopenchrom:workflow2 and testopenchrom2:workflow4"

### ❌ This Skill Does NOT Handle:

- **Detailed Dogme mode-specific interpretation** → Routes to mode-specific skills only after `workflow_key=dogme` is confirmed
  ### BAM Detail Fallback (Supported Tools Only)

  If the user asks for BAM details (header, mapped/unmapped, alignment summary):

  1. **First call `list_job_files`** for the run/workflow.
  2. Locate the BAM and nearby alignment/QC files from the listing.
  3. Use only supported Analyzer tools (`find_file`, `read_file_content`,
     `parse_csv_file`, `parse_bed_file`) to inspect summary outputs.

  **Never call unsupported tools such as `show_bam_details`.**

  - Dogme DNA results → `[[SKILL_SWITCH_TO: run_dogme_dna]]`
  - Dogme RNA results → `[[SKILL_SWITCH_TO: run_dogme_rna]]`
  - Dogme cDNA results → `[[SKILL_SWITCH_TO: run_dogme_cdna]]`

- **Submitting new jobs** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
  - "Run analysis on my data"
  - "Submit a new job"
  - "Process these pod5 files"

- **Remote execution / SSH / SLURM profile setup** → `[[SKILL_SWITCH_TO: remote_execution]]`
  - "Show my SSH profiles"
  - "What are my SLURM defaults for hpc3?"
  - "List remote files on the cluster"

- **ENCODE data lookup** → `[[SKILL_SWITCH_TO: encode_search]]`
  - "How many files for ENCSR160HKZ?"
  - "Search ENCODE for experiments"
  - "What's available for this biosample?"

- **ENCODE data download/processing** → `[[SKILL_SWITCH_TO: encode_longread]]`
  - "Download and process ENCODE experiment"

### 🔀 General Routing Rules:

**This skill is the main entrypoint for completed-workflow analysis:**
1. Verifies the workflow exists and gets `workflow_key` plus mode
2. Keeps `reconcile_bams`, `haplotype_with_vcf`, `differential_expression`, and `wf_pore_c` inside this skill for family-specific summary and file browsing
3. Switches to Dogme DNA/RNA/cDNA skills only when the workflow family is Dogme and the user needs mode-specific interpretation

**If user asks about:**
- **New local data** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **SSH profiles / SLURM account-partition / remote cluster setup** → `[[SKILL_SWITCH_TO: remote_execution]]`
- **ENCODE searches** → `[[SKILL_SWITCH_TO: encode_search]]`
- **General help** → `[[SKILL_SWITCH_TO: welcome]]`

**When uncertain:** Prefer staying on this skill unless the summary clearly shows a Dogme workflow that needs DNA/RNA/cDNA interpretation.

## Inputs

* `work_dir`: (String) The workflow directory path of the completed job to analyze (preferred)
* `run_uuid`: (String, Legacy) The UUID of the completed job — only used as fallback
* `analysis_type`: (String, Optional) Type of analysis: "qc_report", "summary", "detailed", "files_only"
* `bed_file_path`: (String, Optional) Absolute or workflow-relative BED file path when the user asks for chromosome-region counts
* `modification_name`: (String, Optional) Modification token such as `inosine` or `m6A` when the user asks for modification counts by chromosome
* `project_slug:workflowN`: (String, Optional) Cross-project shorthand for server-hosted completed workflows. For region-overlap requests, resolve this to the workflow's `openChromatin/` folder.

## Cross-Workflow Region Overlap Shorthand

When the user asks to compare completed workflow regions using a shorthand like:

`testslopenchrom:workflow2`

interpret that as the server-hosted workflow folder for that project slug and workflow number. For Fiber-seq open-chromatin overlap/Venn requests:

1. Resolve each `project_slug:workflowN` reference to that workflow's `openChromatin/` folder.
2. Use Analyzer tool `compare_bed_region_overlaps`.
3. Default both folder patterns to `*.m6Aopen.bed`.
4. Default `min_overlap_bp=1` so even a 1 bp interval overlap counts as shared.
5. Let Cortex render the Venn from the returned overlap-membership dataframe.

Canonical example:

```text
[[DATA_CALL: service=analyzer, tool=compare_bed_region_overlaps, folder_a=testslopenchrom:workflow2, folder_b=testopenchrom2:workflow4, pattern_a=*.m6Aopen.bed, pattern_b=*.m6Aopen.bed, min_overlap_bp=1]]
```

Do not ask the user for explicit BED paths when the `project_slug:workflowN` references are already present.

## Bundled Script: Count BED Regions per Chromosome

Use the bundled helper script at `skills/analyze_job_results/scripts/count_bed.py` when the user asks to count BED regions per chromosome or to count a modification by chromosome from workflow BED files.

Treat this as a local utility-script execution, not as a Dogme workflow submission.

### When to use it

Use this path for requests such as:
- "Count BED regions by chromosome"
- "How many regions are on each chromosome in this BED file?"
- "Run the BED chromosome counter on this file"
- "Count inosine modifications by chromosome"
- "Count m6A modifications by chromosome"

### Required information

For explicit BED-file requests, ensure you have the BED file path.

For modification-count requests, use the workflow's `bedMethyl` folder and match files named like:

`<sample>.<genome>.plus.<modification>.filtered.bed`

or

`<sample>.<genome>.plus.<modification>.filtered.bed.gz`

and

`<sample>.<genome>.minus.<modification>.filtered.bed`

or

`<sample>.<genome>.minus.<modification>.filtered.bed.gz`

Use both plus and minus files when both exist for the requested modification. If only one exists, use the one that exists.

### Execution behavior

Once a BED path is available:
- Do NOT tell the user to run the script manually
- Do NOT route to Dogme DNA/RNA/CDNA skills
- Do NOT treat this like a Nextflow pipeline run
- Do NOT request approval for this lightweight utility script
- Do treat it as a local utility execution using Launchpad tool `run_allowlisted_script` and allowlisted script id `analyze_job_results/count_bed`

### How the LLM should plan it

Use a two-step plan when the user gives only a filename or workflow-relative BED path:

1. Resolve the BED file with Analyzer:

```text
[[DATA_CALL: service=analyzer, tool=find_file, file_name=<bed_file_name>, work_dir=<work_dir>]]
```

2. After the BED file is resolved, run the utility script through Launchpad's dedicated script tool.

If the BED path is already explicit and absolute, call Launchpad directly:

```text
[[DATA_CALL: service=launchpad, tool=run_allowlisted_script, script_id=analyze_job_results/count_bed, script_args=["--json", "<absolute_bed_file_path>"]]]
```

If you first call `find_file`, the runtime will automatically use the resolved `primary_path` to run `analyze_job_results/count_bed` for this BED chromosome-count intent.

For modification-count requests such as "count inosine modifications by chromosome":

1. List `.bed` files in the workflow's `bedMethyl` folder:

```text
[[DATA_CALL: service=analyzer, tool=list_job_files, work_dir=<work_dir>/bedMethyl, extensions=.bed, max_depth=1]]
```

2. The runtime will automatically select matching plus/minus files for the requested modification and run `analyze_job_results/count_bed` on all matches.

3. The resulting dataframe will contain per-sample, per-genome, per-modification chromosome counts, with plus/minus files summed together within the same sample/genome/modification/chromosome bucket.

### Exact script-run parameters

When preparing the local utility execution, use these values:
- `tool`: `run_allowlisted_script`
- `script_id`: `analyze_job_results/count_bed`
- `script_args`: `["--json", "<bed_file_path>", ...]`
- `script_working_directory`: optional; omit unless a specific working directory is needed

### Response format

After the utility runs, summarize the result clearly and keep the dataframe available for plotting.

Use this structure:

```text
BED chromosome counts for <bed_file_path or modification query>:

<brief summary of the counts, explicitly noting the genomes represented>

This was produced by the local allowlisted utility script `analyze_job_results/count_bed`, and the dataframe can be plotted later.
```

### Script behavior

The script:
- Reads the first BED column as the chromosome name
- Counts one region per non-empty BED record
- Accepts one or more BED files
- Sums matching plus/minus BED files together when multiple files are provided
- Tracks `Sample`, `Genome`, `Modification`, `Chromosome`, and `Count` in structured output
- Ignores blank lines and header/meta lines beginning with `#`, `track`, or `browser`
- Emits JSON dataframe output when `--json` is provided

## Plan Logic

### IMMEDIATE EXECUTION FOR ANALYSIS REQUESTS

Start with the workflow summary so you have both `workflow_key` and `mode`:

```
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=<work_dir>]]
```

If the user asks for a file inventory, available outputs, or a quick overview, you may also call:

```
[[DATA_CALL: service=analyzer, tool=list_job_files, work_dir=<work_dir>, extensions=.csv,.tsv,.bed,.txt]]
```

### Routing Logic

After `get_analysis_summary`, branch by `workflow_key` first:

- If `workflow_key` is `reconcile_bams`, stay on this skill and summarize reconcile outputs.
- If `workflow_key` is `haplotype_with_vcf`, stay on this skill and summarize haplotype outputs.
- If `workflow_key` is `wf_pore_c`, stay on this skill and summarize contact-map outputs.
- If `workflow_key` is `dogme` or missing, use `mode` to decide whether to switch to a Dogme mode-specific skill.

Only Dogme workflows should switch away from this skill:

```
# Dogme-only routing
# If mode equals "CDNA" exactly:
[[SKILL_SWITCH_TO: run_dogme_cdna]]
# If mode equals "DNA" exactly:
[[SKILL_SWITCH_TO: run_dogme_dna]]
# If mode equals "RNA" exactly:
[[SKILL_SWITCH_TO: run_dogme_rna]]
```

For `reconcile_bams`, `haplotype_with_vcf`, and `wf_pore_c`:
- Do NOT switch to Dogme skills just because the workflow contains BAMs or TSVs.
- Do summarize the workflow-family-specific outputs directly from Analyzer results.
- Do keep follow-up file browsing, report reading, and summary requests on this skill unless the user explicitly asks to switch contexts.

---

### 1. Information Gathering

**BEFORE DOING ANYTHING ELSE - WORKFLOW DIRECTORY CONFIRMATION**

The system injects `[CONTEXT: work_dir=...]` at the start of the user message. Use that work_dir directly.

**STEP 1: Check Current Request FIRST**

**PRIORITY 1 - CONTEXT INJECTION:**
- Check for `[CONTEXT: work_dir=...]` at the start of the user message
- If present, use that work_dir directly in your DATA_CALL
- Do NOT look elsewhere
- Do NOT use a workflow directory from earlier in the conversation

**PRIORITY 2 - Check Conversation History**

Only if no `[CONTEXT: ...]` line, scan recent conversation history:
- Look for "Workflow:" in recent "Analysis Ready" messages
- Look for sample names mentioned with "completed successfully" or "Job completed"
- The most recent completed job's workflow directory is what the user wants analyzed

**STEP 2: If work_dir Found (Context or History)**
- Use that work_dir immediately
- Mention to the user which job you're analyzing: "I'll analyze the recently completed job {sample_name}"
- Proceed to job verification

**STEP 3: If NO work_dir Found Anywhere**
Ask the user for the information:

Example questions:
- "Which job would you like me to analyze? Please provide the sample name."
- "What type of analysis would you like? (QC report, summary, or detailed file analysis)"

**DO NOT include [[APPROVAL_NEEDED]] when asking for information - just ask the questions.**

**IMPORTANT:** If you find a workflow in the conversation history, DO NOT submit a new job. Use the EXISTING workflow directory.

---

**⚠️ CRITICAL: ALWAYS Use the Current UUID from This Conversation**

The analysis tools require the actual job UUID. **Do NOT use old UUIDs from previous analysis sections.**

**RULE:** Use ONLY the work_dir from the CURRENT analysis request, which is typically from the `[CONTEXT: work_dir=...]` line injected by the system or the most recent job completion message.

**Common mistake:** An EARLIER section of this conversation might show a different workflow. If the user now asks "analyze jamshid", verify there's NOT a MORE RECENT workflow for that sample. Always use the LATEST one.

**Example:**
```
Context injection shows:
[CONTEXT: work_dir=/media/.../project/workflow2, sample=jamshid]

[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=/media/.../project/workflow2]]
```

**⚠️ If you get "File not found" error:** Check your work_dir against the context injection. Wrong directory is the most common cause.

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

⚠️ **The DataCall below MUST execute right now with the work_dir from context:**

```
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=<work_dir>]]
```

**After you get the response with `workflow_key` and `mode`:**

- If `workflow_key` is `reconcile_bams`, `haplotype_with_vcf`, or `wf_pore_c`, stay on this skill and handle the requested file parsing directly.
- If `workflow_key` is `dogme` or missing, then switch to the appropriate Dogme skill based on `mode`.

Dogme-only switch rules:
- If `mode=CDNA`: `[[SKILL_SWITCH_TO: run_dogme_cdna]]`
- If `mode=DNA` or `mode=Fiber-seq`: `[[SKILL_SWITCH_TO: run_dogme_dna]]`
- If `mode=RNA`: `[[SKILL_SWITCH_TO: run_dogme_rna]]`

**Critically important:**
- ❌ DO NOT show/display the get_analysis_summary response to the user
- ❌ DO NOT say "Here's the summary" or "Job found"
- ✅ DO immediately trigger [[SKILL_SWITCH_TO: ...]] without any other messages
- ✅ The Dogme skill will receive the UUID and filename and handle everything else

**Then:** The Dogme skill will execute file finding and parsing automatically.

**⚠️ CRITICAL RULES:**
- ❌ DO NOT spend time describing "I will parse the file"
- ❌ DO NOT explain the steps first
- ❌ DO NOT print warnings without executing
- ❌ DO NOT display ANY output before switching skills for Dogme workflows
- ❌ DO NOT call multiple tools before deciding the workflow family
- ✅ DO use the work_dir from the context injection
- ✅ DO call get_analysis_summary immediately
- ✅ DO switch to a Dogme skill only when the workflow family is Dogme
- ✅ DO keep non-Dogme parsing on this skill

### 2. Job Verification and Mode-Aware Routing

Once you have the workflow directory, get the analysis summary first:

```
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=<work_dir>]]
```

The summary includes the job's **workflow family** via `workflow_key`, plus **mode** for Dogme workflows.

**Workflow family → next action:**
- `reconcile_bams` → stay on this skill and summarize reconcile manifests, BAM outputs, annotation files, and reports
- `haplotype_with_vcf` → stay on this skill and summarize haplotyped BAMs plus summary TSVs
- `wf_pore_c` → stay on this skill and summarize contact-map outputs
- `dogme` or missing → use `mode` to switch to the Dogme interpretation skill

**Dogme mode → analysis skill:**
- CDNA → `[[SKILL_SWITCH_TO: run_dogme_cdna]]`
- DNA or Fiber-seq → `[[SKILL_SWITCH_TO: run_dogme_dna]]`
- RNA (direct RNA) → `[[SKILL_SWITCH_TO: run_dogme_rna]]`

**⚠️ CRITICAL: After receiving the analysis summary response:**
- ❌ DO NOT dump raw summary JSON to the user
- ❌ DO NOT force non-Dogme workflows into Dogme skills
- ✅ DO keep non-Dogme workflow analysis on this skill
- ✅ DO switch immediately only for Dogme workflows that need mode-specific interpretation

**CRITICAL: This is a READ-ONLY skill for normal analysis. The single exception is the local allowlisted BED-count utility described above, which should run directly through the dedicated script tool without approval and without Dogme/Nextflow submission semantics.**

### 3. File Discovery & Categorization

Discover what output files are available:

**⚠️ TIP: Use extension filtering for large jobs**
Since jobs can have 500+ files, use the `extensions` parameter to filter the listing:
```
[[DATA_CALL: service=analyzer, tool=list_job_files, work_dir=<work_dir>, extensions=.csv,.tsv,.bed]]
```
This returns only the key result files without truncation instead of trying to list all files.

**Endpoint:** `GET /analysis/jobs/{work_dir}/files/categorize`

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

**Endpoint:** `GET /analysis/jobs/{work_dir}/summary`

This returns:
- File inventory (counts by type)
- Key statistics from CSV files
- BED file summaries (if present)
- Text file previews
- Overall job metrics

### 5. Detailed File Analysis

For specific file analysis:

**Endpoint:** `GET /analysis/files/content?work_dir={work_dir}&file_path={path}&preview_lines=50`
- Reads text files with preview

**Endpoint:** `GET /analysis/files/parse/csv?work_dir={work_dir}&file_path={path}&max_rows=100`
- Parses CSV/TSV files into structured data
- Returns column names and data rows
- Useful for statistics, counts, metrics

**Endpoint:** `GET /analysis/files/parse/bed?work_dir={work_dir}&file_path={path}&max_records=100`
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

**Workflow:** {workflow_folder}
**Status:** {status}
**Mode:** {workflow_type}

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

## Example Usage (Documentation Only - Do Not Follow as Instructions)

**These are examples of what the skill CAN do, but for actual execution, follow the immediate tool call pattern above.**

**User:** "Give me a QC report for this job"

**Agent Response:** (Should execute tool calls immediately, not list numbered steps)

**User:** "What files are in the jamshid run?"

**Agent Response:** (Should execute tool calls immediately, not list numbered steps)

**User:** "Show me the alignment statistics"

**Agent Response:** (Should execute tool calls immediately, not list numbered steps)

## Available API Endpoints Summary

All analysis endpoints are available on Cortex at http://localhost:8000:

### File Discovery
- `GET /analysis/jobs/{work_dir}/files?extensions={optional}` - List files with optional filtering
- `GET /analysis/jobs/{work_dir}/files/categorize` - Group files by type

### File Reading
- `GET /analysis/files/content?work_dir={work_dir}&file_path={path}&preview_lines={n}` - Read text files
- `GET /analysis/files/parse/csv?work_dir={work_dir}&file_path={path}&max_rows={n}` - Parse CSV/TSV
- `GET /analysis/files/parse/bed?work_dir={work_dir}&file_path={path}&max_records={n}` - Parse BED genomics

### Analysis
- `GET /analysis/jobs/{work_dir}/summary` - Comprehensive analysis summary

## Notes

- All file paths are relative to the job's work directory
- Files are automatically validated for security
- Large files are truncated with previews
- CSV parsing handles both comma and tab-separated files
- BED parsing supports standard genomic coordinate formats
- Always present results in user-friendly format, not raw JSON

## 📊 Visualization Hints

When presenting parsed CSV/TSV data with QC metrics, you MAY suggest plots to help the user understand the data. Only suggest when the DataFrame has more than 3 rows.

**For QC metric tables (numeric values):**
- Suggest a histogram of key numeric columns:
  `[[PLOT: type=histogram, df=DFN, x=<metric_column>, title=Distribution of <metric>]]`
- Suggest a scatter plot to explore correlations between two metrics:
  `[[PLOT: type=scatter, df=DFN, x=<metric1>, y=<metric2>, title=<metric1> vs <metric2>]]`

**For file inventories or categorized results:**
- Suggest a bar chart of file types:
  `[[PLOT: type=bar, df=DFN, x=Extension, agg=count, title=Files by Type]]`

**For BED genomic data:**
- Suggest a histogram of region scores:
  `[[PLOT: type=histogram, df=DFN, x=score, title=Score Distribution]]`
- Suggest a bar chart of chromosome distribution:
  `[[PLOT: type=bar, df=DFN, x=chrom, agg=count, title=Regions per Chromosome]]`

**For correlation analysis (multiple numeric columns):**
- Suggest a heatmap:
  `[[PLOT: type=heatmap, df=DFN, title=Metric Correlation Matrix]]`

Replace `DFN` with the actual DF number. Only include these tags if the user asks for visualization or if a chart would be informative.

## Plan Chains

### count_bed_by_chromosome
- description: Run the allowlisted BED chromosome-count utility on a specific BED file and keep the result as a dataframe
- trigger: count|summarize|tally|show + bed + chromosome|chromosomes|chr
- steps:
  1. FIND_FILE: Resolve the BED file path in the current workflow when needed
  2. RUN_SCRIPT: Run `analyze_job_results/count_bed` against the resolved BED file path
  3. WRITE_SUMMARY: Return chromosome counts clearly to the user
- auto_approve: true

### count_modification_by_chromosome
- description: Count a named modification by chromosome from the workflow's `bedMethyl` plus/minus BED files and keep the result as a dataframe
- trigger: count|summarize|tally|show + <modification> + modifications + chromosome|chromosomes|chr
- steps:
  1. LIST_FILES: Inspect `<work_dir>/bedMethyl` for matching `plus` and `minus` BED files for the requested modification
  2. RUN_SCRIPT: Run `analyze_job_results/count_bed` on all matching files, summing counts within each sample/genome/modification/chromosome
  3. WRITE_SUMMARY: Return a concise summary and mention that the dataframe can be plotted later
- auto_approve: true
