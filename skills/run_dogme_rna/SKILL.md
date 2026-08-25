# Skill: Dogme Direct RNA Analysis (`run_dogme_rna`)

## Description

This skill provides **downstream analysis interpretation** for completed Dogme Direct RNA jobs. It is activated by `analyze_job_results` when the job mode is RNA.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Interpreting RNA modifications (m6A, pseudouridine, inosine, m5C, and Nm)
- RNA modification frequency and stoichiometry analysis
- Direct RNA alignment and mapping quality assessment
- Transcript quantification from direct RNA reads
- Poly(A) tail length analysis
- Explaining QC metrics specific to direct RNA sequencing

**Example questions:**
- "Show me the m6A modification sites"
- "What's the RNA modification frequency?"
- "Parse the RNA mod BED file"
- "Analyze poly(A) tail lengths"

### ❌ This Skill Does NOT Handle:

- **Submitting new jobs** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
  - "Analyze my pod5 files"
  - "Run Dogme on my RNA data"
  - "Submit a new direct RNA job"

- **ENCODE data lookup** → `[[SKILL_SWITCH_TO: encode_search]]`
  - "How many BAM files are there for ENCSR160HKZ?"
  - "What RNA-seq experiments are available for K562?"
  - "Find ENCODE direct RNA data"

- **Analyzing different jobs** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
  - "Check results for job XYZ" (when switching to a different job)
  - "Give me QC for another sample"

- **DNA modifications or cDNA expression** → Direct RNA mode only
  - For DNA methylation, use DNA mode
  - For standard gene expression, cDNA mode is more appropriate

### 🔀 General Routing Rules:

**When the user's question is outside direct RNA analysis:**
- **New data / file paths** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE accessions/experiments** → `[[SKILL_SWITCH_TO: encode_search]]`
- **Different job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
- **General help / unclear intent** → `[[SKILL_SWITCH_TO: welcome]]`

**When uncertain:** If the question is clearly outside direct RNA result interpretation, switch to the appropriate skill rather than saying "I can't help."

## Direct RNA Pipeline Overview

The Dogme RNA pipeline performs:
1. **Basecalling** (pod5 → unmapped bam) — using Dorado with RNA modification-aware models
2. **Alignment** (unmapped bam → mapped bam) — mapping to transcriptome + genome reference
3. **RNA modification calling** — detecting m6A, pseudouridine (pseU), inosine, m5C and Nm separately for plus and minus strand
4. **Transcript quantification** — counting reads per gene/transcript in `annot/` folder
6. **QC and summary reports**

## Key Output Files to Examine

### Alignment Statistics
- `*.flagstat.txt` — samtools flagstat (mapped reads, supplementary alignments)
- `*.stats.csv` — alignment summary (note: direct RNA has lower mapping rates than cDNA)
- `*.mapping_stats.txt` — read length and mapping quality distributions

### Modification Files (RNA has modifications)
- `*.m6A.filtered.bed` or `*.m6A.filtered.bed.gz` — m6A modification calls with per-site frequencies
- `*.pseU.filtered.bed` or `*.pseU.filtered.bed.gz` — pseudouridine modification calls (if model supports)
- `*.m5C.filtered.bed` or `*.m5C.filtered.bed.gz` — m6A modification calls with per-site frequencies
- `*.inosine.filtered.bed` or `*.inosine.filtered.bed.gz` — inosine modification calls with per-site frequencies
- `*.Nm.filtered.bed` or `*.Nm.filtered.bed.gz` — Nm modification calls with per-site frequencies

### Transcript/Gene Counts in `annot/` folder
- `*_qc_summary.csv` — gene-level expression counts
- `*dogme_abundance.tsv` — transcript-level isoform quantification

### QC Reports
- `qc_summary.summary` — comprehensive QC metrics
- `*.html` — visual QC reports (if generated)

## How to Interpret Results

### Alignment Quality (Direct RNA specifics)
- **Mapped reads > 70%** = good for direct RNA (lower than cDNA is normal)
- **Median read length**: direct RNA reads are typically 500bp-2kb
- **Supplementary alignments**: common in direct RNA due to RNA structure

### RNA Modification Analysis
- **m6A sites**: enriched at DRACH motifs (D=A/G/U, R=A/G, H=A/C/U)
- **m6A frequency per site**: typically 10-80% at true sites
- **Pseudouridine (Ψ)**: enriched in rRNA and tRNA, also found in mRNA
- **Filter by coverage**: sites with < min_cov reads are unreliable
- **Compare to known databases**: m6A-Atlas, RMBase for validation

### Expression Analysis
- **Gene counts**: compare across samples for differential expression
- **Isoform ratios**: direct RNA preserves full-length transcripts
- **Read length vs gene coverage**: longer reads = better isoform resolution

### Poly(A) Tail Length
- **Median tail length**: typically 50-250nt for mRNA
- **Tail length vs expression**: shorter tails often correlate with mRNA decay
- **Distribution shape**: bimodal distributions may indicate regulation

## Quick Workflow: Parse a File

**When asked to parse a file, check for `[CONTEXT: work_dir=...]` in the user message first.**
The system injects the workflow directory automatically — use it directly in your DATA_CALL, don't search for it.

Follow the comprehensive workflow in the included guide: `[INCLUDED REFERENCE: DOGME_QUICK_WORKFLOW_GUIDE.md]`

That guide includes:
- **Filename retrieval** if switched from analyze_job_results
- **STEP 1-5:** Find file, extract path, parse, validate, present
- **Directory prefix requirement** (critical for success)

**RNA-specific tools:**
- Modification sites: `parse_bed_file` for `*.m6A.filtered.bed`, `*.pseU.filtered.bed`
- Gene counts: `parse_csv_file` for expression data
- Modification summaries: list `bedMethyl/*.filtered.bed` and `bedMethyl/*.filtered.bed.gz` files and use the allowlisted script `analyze_job_results/count_bed` to count sites across plus/minus files

### Modification Summary Requests

There is no `modkit_summary` file in the current RNA workflow outputs. For requests like:
- "Show me the modification summary"
- "Summarize the RNA modifications"
- "How many modification sites were found?"

use the workflow's `bedMethyl/` folder instead.

Do not reject or redirect a workflow-specific analysis request just because that workflow is marked `FAILED` or `CANCELLED`. If the user explicitly asks for `workflow1`, `/use workflow1`, or another named workflow and result files are present, analyze that workflow's files directly. A cancelled or failed workflow can still contain synced outputs worth summarizing.

When a workflow-specific RNA follow-up targets a failed/cancelled workflow, prefer this order:
1. Check the requested workflow's relevant output folder such as `bedMethyl/`, `annot/`, or other expected result paths
2. If the files exist, continue the analysis for that workflow instead of switching to a different completed workflow
3. Only fall back to another workflow when the explicitly requested workflow truly has no relevant result files

Preferred flow:
1. List `.bed` and `.bed.gz` files in `<work_dir>/bedMethyl`
2. Use only `*.filtered.bed` or `*.filtered.bed.gz` files that include explicit modification names such as `m6A`, `inosine`, `m5C`, `pseU`, or `Nm`
3. Sum plus/minus files with the allowlisted script `analyze_job_results/count_bed`
4. Present totals by modification, noting that the underlying dataframe remains available for plotting or chromosome-level follow-up

When `count_bed` returns chromosome-level rows, the first markdown summary table must show numeric totals per modification by summing the `Count` column across chromosomes. Use a `Modification | Count` style summary, not a `Detected` or `Present` status table. Keep the detailed per-chromosome dataframe available for later plotting.

Do not search for `modkit_summary`, `modification_summary.txt`, or similar nonexistent summary files for this workflow.

### RNA-Specific Notes

**Files to search for:**
- Modification sites: `find_file(work_dir=..., file_name=m6A)` or `modifications`
- Gene expression: `find_file(work_dir=..., file_name=gene_counts)` or `transcript`
- Alignment stats: `find_file(work_dir=..., file_name=stats)` or `flagstat`

**Typical directories:**
- `bedMethyl/` — RNA modification calls
- `counts/` — 
- `annot/` — alignment statistics, gene and transcript quantification 

**Parsing and interpreting RNA results:**
- Modification BED → use `parse_bed_file` → shows m6A, pseudouridine sites with frequencies
- Gene counts CSV → use `parse_csv_file` → shows transcript abundance
- Alignment stats → use `read_file_content` → shows mapping quality and coverage
- m6A sites enriched at DRACH motifs indicate authentic modification sites

---

## Full Analysis Workflow

When user says "analyze the results":

**STEP 1:** Get the analysis summary
```
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=<work_dir>]]
```

**STEP 2:** Use the summary only as the starting point, not the final answer
- Do not stop after file counts or file-availability statements
- Treat the summary as evidence for which result files should be parsed next

**STEP 3:** Parse the most informative key outputs immediately when they are available
- Parse the main QC or quantification CSV when a `*_qc_summary.csv` or similar file is present
- Parse the main stats CSV when a `*_final_stats.csv` or similar file is present
- If RNA modification BED files are available, summarize them from `bedMethyl/` using the shared modification-summary flow
- See [DOGME_QUICK_WORKFLOW_GUIDE.md](DOGME_QUICK_WORKFLOW_GUIDE.md) for the complete step-by-step workflow

**STEP 4:** Write a detailed first-pass analysis
- Include an **Overall Assessment** that explains what completed successfully and what the file set implies about the run
- Include **Key Metrics** using parsed values whenever available; if a key metric is not yet parsed, say which file contains it
- Include **Reference-Specific Findings** when the reference genome or annotation target is clear
- Include **QC Concerns or Limitations** grounded in parsed stats, missing artifacts, or workflow-specific caveats
- Include **Recommended Next Steps** tied to the observed outputs

**STEP 5:** Prefer evidence over generic filler
- Base conclusions on parsed CSV or BED content whenever possible
- Avoid vague statements like "QC Summary: Available" unless followed by interpretation
- Mention notable files after the analytic sections above, not instead of them

---

## KEY RULES

**DO:**
- Reference [DOGME_QUICK_WORKFLOW_GUIDE.md](DOGME_QUICK_WORKFLOW_GUIDE.md) for the standard workflow
- Execute tool calls immediately — don't explain what you're about to do
- Present results with clear explanations of what they mean
- For generic "analyze results" requests, default to a detailed post-run analysis with sections such as Overall Assessment, Key Metrics, QC Concerns or Limitations, and Recommended Next Steps
- Offer suggestions for further analysis

**DON'T:**
- Explain your process step-by-step before executing
- Say "the query did not return expected data" when parse succeeds
- Stop after reporting file counts and file availability for a generic analysis request
- Ask permission for obvious next steps
- Forget the directory prefix in file_path parameter
- Ask permission for obvious next steps ("Would you like me to parse this file?")
- Get stuck in explanation loops — act first, explain results
````

**STEP 5:** Parse gene/transcript counts using FULL path from STEP 3
```
[[DATA_CALL: service=analyzer, tool=parse_csv_file, work_dir=<work_dir>, file_path=counts/sample_name_gene_counts.csv]]
```

**STEP 6:** Parse modification BED files to get detailed m6A and other modification locations from STEP 3
```
[[DATA_CALL: service=analyzer, tool=parse_bed_file, work_dir=<work_dir>, file_path=bedMethyl/sample_name.genomeRef.plus.mod.filtered.bed.gz]]
[[DATA_CALL: service=analyzer, tool=parse_bed_file, work_dir=<work_dir>, file_path=bedMethyl/sample_name.genomeRef.minus.mod.filtered.bed.gz]]
```

**STEP 7:** Present results with RNA-specific interpretation (modification sites, expression levels)