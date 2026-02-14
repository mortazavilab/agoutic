# Skill: Dogme cDNA Analysis (`run_dogme_cdna`)

## Description

This skill provides **downstream analysis interpretation** for completed Dogme cDNA jobs. It is activated by `analyze_job_results` when the job mode is CDNA.

**This skill does NOT submit jobs.** Job submission is handled by `analyze_local_sample` (local data) or `ENCODE_LongRead` (ENCODE data).

If the user wants to submit a new job:
```
[[SKILL_SWITCH_TO: analyze_local_sample]]
```

## cDNA Pipeline Overview

The Dogme cDNA pipeline performs:
1. **Basecalling** (pod5 → fastq) — using Dorado
2. **Alignment** (fastq → bam) — mapping to transcriptome + genome reference
3. **Transcript quantification** — counting reads per gene and transcript isoform
4. **Isoform analysis** — full-length transcript identification
5. **QC and summary reports**

**⚠️ cDNA does NOT have modification calling** — unlike direct RNA and DNA modes, cDNA sequencing does not preserve base modifications (the reverse transcription step erases them).

## Key Output Files to Examine

### Alignment Statistics
- `*.flagstat.txt` — samtools flagstat (mapped reads, duplicates, pairs)
- `*.stats.csv` — alignment summary statistics
- `*.mapping_stats.txt` — read length and mapping quality distributions

### Transcript/Gene Counts (primary output for cDNA)
- `*.counts.csv` or `*.gene_counts.csv` — gene-level expression counts
- `*.transcript_counts.csv` — transcript-level isoform quantification
- `*.junctions.bed` — splice junction support
- `*.isoform_classification.csv` — isoform categorization (known, novel, fusion)

### QC Reports
- `*qc_summary*` — comprehensive QC metrics
- `*.html` — visual QC reports (if generated)

### NO Modification Files
cDNA mode does not produce:
- ❌ No `*.m6A.bed`
- ❌ No `*.5mC.bed`
- ❌ No `*.modkit_summary.txt`
- ❌ No `*.mod_freq.csv`

If the user needs modification data, they should re-run with direct RNA (for RNA mods) or DNA (for DNA mods) mode.

## How to Interpret Results

### Alignment Quality (cDNA specifics)
- **Mapped reads > 85%** = good for cDNA
- **Median read length**: cDNA reads are typically 1-5kb (longer than direct RNA)
- **Full-length reads**: higher percentage = better library quality

### Expression Analysis (main focus for cDNA)
- **Gene counts**: primary metric for differential expression
- **TPM/FPKM normalization**: for cross-sample comparison
- **Isoform ratios**: cDNA captures full-length transcripts with high accuracy
- **Novel isoforms**: unannotated splice junctions may indicate novel transcripts
- **Fusion transcripts**: chimeric reads spanning two genes

### Library Quality
- **5' to 3' coverage ratio**: even coverage indicates good full-length capture
- **cDNA size distribution**: should match expected transcript lengths
- **PCR duplicate rate**: high rates indicate low input or over-amplification

## Analysis Workflow

When analyzing a completed cDNA job:

**STEP 1:** Get the analysis summary
```
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=<uuid>]]
```

**STEP 2:** Check for expression and isoform files
```
[[DATA_CALL: service=server4, tool=categorize_job_files, run_uuid=<uuid>]]
```

**STEP 3:** Parse gene counts
```
[[DATA_CALL: service=server4, tool=parse_csv, run_uuid=<uuid>, file_path=<gene_counts>]]
```

**STEP 4:** Check isoform classification (if available)
```
[[DATA_CALL: service=server4, tool=read_file_content, run_uuid=<uuid>, file_path=<isoform_file>]]
```

**STEP 5:** Present results with cDNA-specific interpretation (expression, isoforms, library quality — NO modification analysis)