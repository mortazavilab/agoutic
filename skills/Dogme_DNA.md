# Skill: Dogme DNA / Fiber-Seq Analysis (`run_dogme_dna`)

## Description

This skill provides **downstream analysis interpretation** for completed Dogme DNA and Fiber-seq jobs. It is activated by `analyze_job_results` when the job mode is DNA.

**This skill does NOT submit jobs.** Job submission is handled by `analyze_local_sample` (local data) or `ENCODE_LongRead` (ENCODE data).

If the user wants to submit a new job:
```
[[SKILL_SWITCH_TO: analyze_local_sample]]
```

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

## Analysis Workflow

**⚠️ CRITICAL: Finding the Job UUID**

The analysis tools require the actual job UUID (e.g., `6a8613d4-832c-4420-927e-6265b614c8b2`), NOT the sample name.

**Where to find it:**
- Look in recent conversation history for auto-analysis messages that contain `**Run UUID:** <backtick>uuid<backtick>`
- Look for job completion messages that show the UUID
- The most recent completed job UUID is typically what the user wants analyzed

**DO NOT use the sample name as the run_uuid parameter.** Extract the actual UUID string.

When analyzing a completed DNA job:

**STEP 1:** Get the analysis summary
```
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=<uuid>]]
```

**STEP 2:** Check modification results — look for BED files with modification data
```
[[DATA_CALL: service=server4, tool=categorize_job_files, run_uuid=<uuid>]]
```

**STEP 3:** Parse key metrics from CSV/stats files
```
[[DATA_CALL: service=server4, tool=read_file_content, run_uuid=<uuid>, file_path=<stats_file>]]
```

**STEP 4:** Present results with DNA-specific interpretation (methylation levels, coverage, modification calling quality)