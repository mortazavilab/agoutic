# Skill: Dogme Direct RNA Analysis (`run_dogme_rna`)

## Description

This skill provides **downstream analysis interpretation** for completed Dogme Direct RNA jobs. It is activated by `analyze_job_results` when the job mode is RNA.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Interpreting RNA modifications (m6A, pseudouridine, inosine)
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
1. **Basecalling** (pod5 → fastq) — using Dorado with RNA modification-aware models
2. **Alignment** (fastq → bam) — mapping to transcriptome + genome reference
3. **RNA modification calling** — detecting m6A, pseudouridine (Ψ), and other RNA modifications
4. **Transcript quantification** — counting reads per gene/transcript
5. **Poly(A) tail length estimation** (if applicable)
6. **QC and summary reports**

## Key Output Files to Examine

### Alignment Statistics
- `*.flagstat.txt` — samtools flagstat (mapped reads, supplementary alignments)
- `*.stats.csv` — alignment summary (note: direct RNA has lower mapping rates than cDNA)
- `*.mapping_stats.txt` — read length and mapping quality distributions

### Modification Files (RNA has modifications)
- `*.modkit_summary.txt` — overall RNA modification summary from modkit
- `*.m6A.bed` — m6A modification calls with per-site frequencies
- `*.pseudoU.bed` — pseudouridine modification calls (if model supports)
- `*.modkit_pileup.bed` — per-position modification pileup across all mod types
- `*.mod_freq.csv` — modification frequency statistics

### Transcript/Gene Counts
- `*.counts.csv` or `*.gene_counts.csv` — gene-level expression counts
- `*.transcript_counts.csv` — transcript-level isoform quantification
- `*.junctions.bed` — splice junction support (for isoform analysis)

### Poly(A) Tail
- `*.polya.csv` — per-read poly(A) tail length estimates
- `*.polya_summary.csv` — summary statistics for tail lengths

### QC Reports
- `*qc_summary*` — comprehensive QC metrics
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
- Modification sites: `parse_bed_file` for `*.m6A.bed`, `*.pseudoU.bed`
- Gene counts: `parse_csv_file` for expression data
- Poly(A) tails: `parse_csv_file` for tail length distributions
- Summaries: `read_file_content` for `*.modkit_summary.txt`

### RNA-Specific Notes

**Files to search for:**
- Modification sites: `find_file(work_dir=..., file_name=m6A)` or `modkit`
- Gene expression: `find_file(work_dir=..., file_name=gene_counts)` or `transcript`
- Poly(A) tails: `find_file(work_dir=..., file_name=polya)`
- Alignment stats: `find_file(work_dir=..., file_name=stats)` or `flagstat`

**Typical directories:**
- `modkit/` — RNA modification calls and pileup data
- `counts/` — gene and transcript quantification
- `annot/` — alignment statistics and summaries

**Parsing and interpreting RNA results:**
- Modification BED → use `parse_bed_file` → shows m6A, pseudouridine sites with frequencies
- Gene counts CSV → use `parse_csv_file` → shows transcript abundance
- Poly(A) data → use `parse_csv_file` → shows tail length distributions
- Alignment stats → use `read_file_content` → shows mapping quality and coverage
- m6A sites enriched at DRACH motifs indicate authentic modification sites
- Poly(A) tail length correlates with stability and translation

---

## Full Analysis Workflow

When user says "analyze the results":

**STEP 1:** Get the analysis summary
```
[[DATA_CALL: service=server4, tool=get_analysis_summary, work_dir=<work_dir>]]
```

**STEP 2:** Present filtered file summary
- The summary now excludes work/intermediate files automatically
- Shows only final result files: txt, csv, bed files

**STEP 3:** Follow the shared workflow for finding and parsing files
- See [DOGME_QUICK_WORKFLOW_GUIDE.md](DOGME_QUICK_WORKFLOW_GUIDE.md) for the complete step-by-step workflow

**STEP 4:** Present results with RNA-specific interpretation
- Modification site locations and frequencies
- Expression levels and isoform composition
- Poly(A) tail length patterns
- Alignment quality and read characteristics
- Enrichment of modifications at known regulatory motifs

---

## KEY RULES

**DO:**
- Reference [DOGME_QUICK_WORKFLOW_GUIDE.md](DOGME_QUICK_WORKFLOW_GUIDE.md) for the standard workflow
- Execute tool calls immediately — don't explain what you're about to do
- Present results with clear explanations of what they mean
- Offer suggestions for further analysis

**DON'T:**
- Explain your process step-by-step before executing
- Say "the query did not return expected data" when parse succeeds
- Ask permission for obvious next steps
- Forget the directory prefix in file_path parameter
- Ask permission for obvious next steps ("Would you like me to parse this file?")
- Get stuck in explanation loops — act first, explain results
````

**STEP 5:** Parse gene/transcript counts using FULL path from STEP 3
```
[[DATA_CALL: service=server4, tool=parse_csv_file, work_dir=<work_dir>, file_path=counts/sample_name_gene_counts.csv]]
```

**STEP 6:** Parse modification BED files to get detailed m6A and other modification locations from STEP 3
```
[[DATA_CALL: service=server4, tool=parse_bed_file, work_dir=<work_dir>, file_path=bedMethyl/sample_name.mod.bed]]
```

**STEP 7:** Present results with RNA-specific interpretation (modification sites, expression levels, poly(A) analysis)