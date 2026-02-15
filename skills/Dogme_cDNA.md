# Skill: Dogme cDNA Analysis (`run_dogme_cdna`)

## Description

This skill provides **downstream analysis interpretation** for completed Dogme cDNA jobs. It is activated by `analyze_job_results` when the job mode is CDNA.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Interpreting cDNA sequencing results (gene counts, transcript quantification, isoforms)
- Explaining QC metrics for completed cDNA jobs
- Parsing and presenting CSV/TSV files from cDNA analysis
- Alignment statistics interpretation for cDNA
- Library quality assessment

**Example questions:**
- "Show me the gene counts for this cDNA run"
- "Parse the QC summary"
- "What's the mapping rate?"
- "How many full-length transcripts were detected?"

### ❌ This Skill Does NOT Handle:

- **Submitting new jobs** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
  - "Analyze my pod5 files"
  - "Run Dogme on my data"
  - "Submit a new cDNA job"

- **ENCODE data lookup** → `[[SKILL_SWITCH_TO: encode_search]]`
  - "How many BAM files are there for ENCSR160HKZ?"
  - "What experiments are available for K562?"
  - "Find ENCODE data for my biosample"

- **Analyzing different jobs** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
  - "Check results for job XYZ" (when switching to a different job)
  - "Give me QC for another sample"

- **Modification calling** → cDNA does not preserve modifications
  - cDNA sequencing cannot detect m6A, 5mC, or other base modifications
  - For modification data, DNA or direct RNA modes are required

### 🔀 General Routing Rules:

**When the user's question is outside cDNA analysis:**
- **New data / file paths** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE accessions/experiments** → `[[SKILL_SWITCH_TO: encode_search]]`
- **Different job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
- **General help / unclear intent** → `[[SKILL_SWITCH_TO: welcome]]`

**When uncertain:** If the question is clearly outside cDNA result interpretation, switch to the appropriate skill rather than saying "I can't help."

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

## Quick Workflow: Parse a File

**See [DOGME_QUICK_WORKFLOW_GUIDE.md](DOGME_QUICK_WORKFLOW_GUIDE.md) for step-by-step instructions.**

That consolidated guide covers:
- UUID verification and selection
- Directory prefix requirement 
- Step-by-step find_file → parse workflow
- Critical rules for all modes
- Data presentation guidelines

### cDNA-Specific Notes

**Files to search for:**
- Gene counts: `find_file(run_uuid=..., file_name=gene_counts)` or `final_stats`
- Transcript info: `find_file(run_uuid=..., file_name=transcript)` or `isoform`
- Alignment stats: `find_file(run_uuid=..., file_name=stats)` or `flagstat`

**Typical directories:**
- `annot/` — final annotations and counts
- `bedMethyl/` — splice junction support  
- `counts/` — gene/transcript quantification

**Parsing and interpreting cDNA results:**
- Gene counts → use `parse_csv_file` → shows known/novel gene discovery
- Transcript counts → use `parse_csv_file` → shows isoform composition
- Alignment stats → use `read_file_content` → shows mapping quality
- Novel transcripts indicate new sequence discoveries
- ISM, NIC, NNC are transcript classification categories (see key files)

---

## Full Analysis Workflow

When user says "analyze the results":

**STEP 1:** Get the analysis summary
```
[[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=<uuid>]]
```

**STEP 2:** Parse cDNA-specific key results
- Gene counts CSV: `parse_csv_file(...)` for expression overview
- Transcript counts: `parse_csv_file(...)` for isoform composition  
- Alignment stats: `read_file_content(...)` for QC metrics

**STEP 3:** Present results with cDNA interpretation
- Library quality (full-length coverage, duplicate rate)
- Expression findings (gene/transcript counts)
- Novel discoveries (new genes/isoforms)
- Mapping statistics

---

## KEY RULES

**DO:**
- Use [DOGME_QUICK_WORKFLOW_GUIDE.md](DOGME_QUICK_WORKFLOW_GUIDE.md) for the complete file parsing workflow
- Execute tool calls immediately — don't explain first
- Present results with clear cDNA-specific interpretation
- Offer suggestions for further analysis

**DON'T:**
- Explain your process before executing
- Say "query did not return expected data" when parsing succeeds
- Ask permission for obvious next steps
- Get stuck in explanation loops

---


````