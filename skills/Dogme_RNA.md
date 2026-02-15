# Skill: Dogme Direct RNA Analysis (`run_dogme_rna`)

## Description

This skill provides **downstream analysis interpretation** for completed Dogme Direct RNA jobs. It is activated by `analyze_job_results` when the job mode is RNA.

**This skill does NOT submit jobs.** Job submission is handled by `analyze_local_sample` (local data) or `ENCODE_LongRead` (ENCODE data).

If the user wants to submit a new job:
```
[[SKILL_SWITCH_TO: analyze_local_sample]]
```

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

## Analysis Workflow

**⚠️ CRITICAL: Finding the Job UUID**

The analysis tools require the actual job UUID (e.g., `6a8613d4-832c-4420-927e-6265b614c8b2`), NOT the sample name.

**Where to find it:**
- Look in recent conversation history for auto-analysis messages that contain `**Run UUID:** <backtick>uuid<backtick>`
- Look for job completion messages that show the UUID
- The most recent completed job UUID is typically what the user wants analyzed

**DO NOT use the sample name as the run_uuid parameter.** Extract the actual UUID string.

When analyzing a completed RNA job:

**STEP 1:** Get the analysis summary
```
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=<uuid>]]
```

**STEP 2:** Check for modification and expression files
```
[[DATA_CALL: service=server4, tool=categorize_job_files, run_uuid=<uuid>]]
```

**STEP 3:** Parse modification summary
```
[[DATA_CALL: service=server4, tool=read_file_content, run_uuid=<uuid>, file_path=<modkit_summary>]]
```

**STEP 4:** Parse gene/transcript counts
```
[[DATA_CALL: service=server4, tool=parse_csv_file, run_uuid=<uuid>, file_path=<counts_file>]]
```

**STEP 5:** Present results with RNA-specific interpretation (modification sites, expression levels, poly(A) analysis)