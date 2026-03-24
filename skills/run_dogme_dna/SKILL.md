# Skill: Dogme DNA / Fiber-Seq Analysis (`run_dogme_dna`)

## Description

This skill provides **downstream analysis interpretation** for completed Dogme DNA and Fiber-seq jobs. It is activated by `analyze_job_results` when the job mode is DNA.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Interpreting DNA methylation (5mC, CpG) results
- Fiber-seq chromatin accessibility analysis (m6A signal)
- DNA modification frequency and coverage analysis
- Explaining QC metrics for completed DNA jobs
- Parsing BED files with modification calls
- Alignment statistics interpretation for DNA

**Example questions:**
- "Show me the DNA methylation patterns"
- "What's the CpG methylation frequency?"
- "Parse the modification BED file"
- "Analyze the Fiber-seq accessibility data"

### ❌ This Skill Does NOT Handle:

- **Submitting new jobs** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
  - "Analyze my pod5 files"
  - "Run Dogme on my DNA data"
  - "Submit a new Fiber-seq job"

- **ENCODE data lookup** → `[[SKILL_SWITCH_TO: encode_search]]`
  - "How many BAM files are there for ENCSR160HKZ?"
  - "What DNA-seq experiments are available for K562?"
  - "Find ENCODE Fiber-seq data"

- **Analyzing different jobs** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
  - "Check results for job XYZ" (when switching to a different job)
  - "Give me QC for another sample"

- **RNA or cDNA analysis** → DNA mode only
  - For RNA modifications or gene expression, different modes are required

### 🔀 General Routing Rules:

**When the user's question is outside DNA analysis:**
- **New data / file paths** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE accessions/experiments** → `[[SKILL_SWITCH_TO: encode_search]]`
- **Different job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
- **General help / unclear intent** → `[[SKILL_SWITCH_TO: welcome]]`

**When uncertain:** If the question is clearly outside DNA result interpretation, switch to the appropriate skill rather than saying "I can't help."

## DNA / Fiber-Seq Pipeline Overview

The Dogme DNA pipeline performs:
1. **Basecalling** (pod5 → fastq) — using Dorado with modification-aware models
2. **Alignment** (fastq → bam) — mapping to reference genome
3. **Modification calling** — extracting DNA methylation (5mC, CpG) from basecalled reads
4. **Fiber-seq analysis** (if applicable) — chromatin accessibility from m6A fiber-seq signal
5. **Coverage and QC reports**

## Key Output Files to Examine

### Alignment Statistics
- `*.flagstat.txt` — samtools flagstat (mapped reads, duplicates, pairs)
- `*.stats.csv` — alignment summary statistics
- `*.coverage.bed` — per-base or per-region coverage

### Modification Files (DNA has modifications)
- `*.modkit_summary.txt` — overall modification summary from modkit
- `*.5mC.bed` or `*.CpG.bed` — CpG methylation BED files with modification frequencies
- `*.modkit_pileup.bed` — per-position modification pileup
- `*.mod_freq.csv` — modification frequency statistics

### Fiber-Seq Specific (if mode includes fiber-seq)
- `*.fiberseq.bed` — fiber-seq accessibility regions
- `*.m6A.bed` — m6A modification calls for chromatin state

### QC Reports
- `*qc_summary*` — comprehensive QC metrics
- `*.html` — visual QC reports (if generated)

## How to Interpret Results

### Alignment Quality
- **Mapped reads > 90%** = good alignment
- **Duplicate rate < 20%** = acceptable
- **Mean coverage > 10x** for modification calling, > 30x ideal

### Methylation Analysis
- **CpG modification frequency**: fraction of reads showing 5mC at each CpG site
- **Global methylation level**: typically 70-80% for mammalian genomes
- **Hypomethylated regions**: promoters, CpG islands (expected low methylation)
- **Differentially methylated regions**: compare across samples or conditions

### Fiber-Seq Interpretation
- **m6A marks** indicate nucleosome-free regions (accessible chromatin)
- **Fiber length** affects resolution of chromatin state calls
- Compare accessibility patterns to known regulatory elements

## Quick Workflow: Parse a File

**When asked to parse a file, check for `[CONTEXT: work_dir=...]` in the user message first.**
The system injects the workflow directory automatically — use it directly in your DATA_CALL, don't search for it.

Follow the comprehensive workflow in the included guide: `[INCLUDED REFERENCE: DOGME_QUICK_WORKFLOW_GUIDE.md]`

That guide includes:
- **Filename retrieval** if switched from analyze_job_results
- **STEP 1-5:** Find file, extract path, parse, validate, present
- **Directory prefix requirement** (critical for success)

**DNA-specific tools:**
- Methylation data: `parse_bed_file` for `*.5mCG.filtered.bed` or `*.5hmCG.filtered.bed`
- Fiber-seq data: `parse_bed_file` for `*.m6Aopen.bed` or `*.m6A.filtered.bed`  
- Alignment stats: `parse_csv_file` for `*.stats.csv` or `read_file_content` for `*.flagstat.txt`

### DNA-Specific Notes

**Files to search for:**
- Methylation data: `find_file(work_dir=..., file_name=5mC)` or `CpG`
- Fiber-seq data: `find_file(work_dir=..., file_name=fiberseq)` or `m6A`
- Alignment stats: `find_file(work_dir=..., file_name=stats)` or `flagstat`

**Typical directories:**
- `bedMethyl/` — methylation calls and CpG frequencies
- `annot/` — alignment statistics and summaries
- `openChromatin/` — modkit region bed file and bg profile

**Parsing and interpreting DNA results:**
- Methylation BED → use `parse_bed_file` → shows CpG modification frequencies
- Fiber-seq data → use `parse_bed_file` → shows chromatin accessibility regions
- Alignment stats → use `parse_csv_file` or `read_file_content` → shows mapping quality and coverage
- Global methylation patterns indicate imprinting status, tissue type, disease state
- Hypomethylated regions often indicate active regulatory elements
- 5mCG is generally repressive 
- 5hmCG can be found at active elements

---

## Full Analysis Workflow

When user says "analyze the results":

**STEP 1:** Get the analysis summary
```
[[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=<work_dir>]]
```

**STEP 2:** Present filtered file summary
- The summary now excludes work/intermediate files automatically
- Shows only final result files: txt, csv, bed files

**STEP 3:** Follow the shared workflow for finding and parsing files
- See [DOGME_QUICK_WORKFLOW_GUIDE.md](DOGME_QUICK_WORKFLOW_GUIDE.md) for the complete step-by-step workflow

**STEP 4:** Present results with DNA-specific interpretation
- Methylation levels and site frequencies
- Coverage and alignment quality
- Modification quality metrics
- Comparison to expected patterns (promoters should be hypomethylated, etc.)

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
````