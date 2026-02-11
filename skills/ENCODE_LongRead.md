# Skill: ENCODE Long-Read Data Processing (Dogme Pipeline)

## Description

This skill handles **downloading and processing ENCODE long-read sequencing data** through the Dogme pipeline. Long-read experiments include direct RNA sequencing, long-read RNA-seq, and whole-genome nanopore sequencing.

**Long-read experiments do NOT have targets** (no ChIP-seq, no TF binding). They produce raw sequencing reads (pod5, fastq) that need basecalling, alignment, and analysis.

## When to Use This Skill

- User wants to **download** ENCODE experiment files
- User wants to **process** long-read data through Dogme pipeline
- User selected a specific experiment and wants to proceed with analysis
- User mentions "download", "process", "run pipeline", "analyze this experiment"

## When NOT to Use This Skill

- User is **searching or browsing** ENCODE → `[[SKILL_SWITCH_TO: ENCODE_Search]]`
- User asks "how many experiments", "what assays", "show me a table" → `[[SKILL_SWITCH_TO: ENCODE_Search]]`
- User has **local data** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- User wants **job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`

## Server 2 Tools Available

This skill can use [[DATA_CALL: consortium=encode, tool=...]] tags for metadata and download operations:

```
get_experiment(accession="ENCSR123ABC")          # Get experiment metadata
get_file_types(accession="ENCSR123ABC")          # List available file types
get_files_summary(accession="ENCSR123ABC")       # File summary with sizes
get_files_by_type(accession="ENCSR123ABC")       # Files organized by type
download_files(accession="ENCSR123ABC", file_types=None)  # Download files
get_file_url(accession="ENCSR123ABC", file_accession="ENCFF123ABC")  # Get URL
```

## Plan Logic

### Step 1: Get Experiment Details

If the user provides an accession, fetch its metadata:

```
I'll fetch the experiment details:

[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR000ABC]]
```

Then check available files:

```
[[DATA_CALL: consortium=encode, tool=get_files_summary, accession=ENCSR000ABC]]
```

### Step 2: Present Download Plan (APPROVAL GATE)

Show the user what will be downloaded and ask for confirmation:

```
**Experiment:** ENCSR000ABC - Direct RNA sequencing (Mus musculus)

**Details:**
- Biosample: C2C12
- Lab: Wold Lab, Caltech
- Replicates: 2
- Status: released

**Available Files:**
- pod5: 4 files (12.5 GB total)
- fastq: 4 files (3.2 GB total)
- bam: 2 files (8.1 GB total)

**Recommended download:** pod5 files (raw data for Dogme pipeline)
**Download location:** ./files/ENCSR000ABC/
**Estimated size:** 12.5 GB

Do you want to proceed with downloading these files?

[[APPROVAL_NEEDED]]
```

### Step 3: Download Files (After Approval)

Once approved, execute the download:

```
[[DATA_CALL: consortium=encode, tool=download_files, accession=ENCSR000ABC, file_types=pod5]]
```

Report results:
- Number of files downloaded
- Total size
- Any failures
- Download location

### Step 4: Classify and Route to Dogme

Based on experiment metadata, determine the analysis mode:

**Assay type → Dogme mode:**
- "Direct RNA sequencing", "direct RNA-seq" → **RNA mode**
- "long read RNA-seq" with cDNA library → **cDNA mode**
- "Whole genome sequencing" → **DNA mode**
- DNA methylation assays → **DNA mode** with modifications

**Extract parameters:**
- `sample_name`: Use accession (e.g., "ENCSR000ABC")
- `input_directory`: Download location (./files/ENCSR000ABC/)
- `reference_genome`: From organism:
  - "Homo sapiens" → "GRCh38"
  - "Mus musculus" → "mm39"
- `mode`: From assay type (DNA/RNA/CDNA)
- `input_type`: From downloaded file type (pod5 or bam)

**Switch to appropriate Dogme skill:**

```
[[SKILL_SWITCH_TO: run_dogme_rna]]
```
or
```
[[SKILL_SWITCH_TO: run_dogme_dna]]
```
or
```
[[SKILL_SWITCH_TO: run_dogme_cdna]]
```

### Step 5: Final Status

```
✅ ENCODE data retrieval completed

Experiment: {accession}
Downloaded: {file_count} files ({total_size})
Location: {output_dir}
Detected assay: {assay_title}
Mode: {mode}

Switching to {target_skill} for pipeline execution.
```

## Error Handling

### No Files Available
```
No downloadable files found for {accession}.

The experiment may be:
- Still in progress (check status)
- Archived or restricted
- Missing the requested file types

Try: [[DATA_CALL: consortium=encode, tool=get_file_types, accession={accession}]]
```

### Download Failures
```
⚠️ Some files failed to download.

Failed: {failed_list}

Options:
1. Retry the download
2. Proceed with successfully downloaded files
3. Select a different experiment
```

### Ambiguous Experiment Type
```
⚠️ Cannot determine analysis mode for {accession}.

Assay: {assay_title}

Please specify which mode to use:
1. DNA (genomic sequencing, methylation)
2. RNA (direct RNA sequencing)
3. cDNA (polyA RNA-seq, cDNA libraries)
```

## Notes

- Long-read experiments typically produce pod5 or fastq files
- Dogme pipeline handles basecalling, alignment, modification calling, and reporting
- Downloads are cached — re-running won't re-download existing files
- File organization: `./files/{accession}/`
