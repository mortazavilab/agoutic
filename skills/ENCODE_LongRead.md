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
get_file_metadata(accession="ENCSR123ABC", file_accession="ENCFF123ABC")  # Get file metadata (needs BOTH)
download_files(accession="ENCSR123ABC", file_types=None)  # Download files
get_file_url(accession="ENCSR123ABC", file_accession="ENCFF123ABC")  # Get file URL (needs BOTH)
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

### Step 2: Present Available Files and Ask User to Choose

Show the user what file types are available and **ask which type to download**:

```
**Experiment:** ENCSR000ABC - Direct RNA sequencing (Mus musculus)

**Details:**
- Biosample: C2C12
- Lab: Wold Lab, Caltech
- Replicates: 2
- Status: released

**Available Files:**
- pod5: 4 files (12.5 GB total) — raw signal, best for full Dogme pipeline
- fastq: 4 files (3.2 GB total) — basecalled reads, skip basecalling step
- bam: 2 files (8.1 GB total) — aligned reads, for re-analysis or modification calling

Which file type would you like to download?
```

**Do NOT auto-select.** The user decides whether to download pod5, fastq, or bam.

### Step 3: Download Files (APPROVAL GATE)

Once the user picks a file type, confirm the download plan:

```
**Download plan:**
- **Files:** 4 pod5 files
- **Location:** ./files/ENCSR000ABC/
- **Estimated size:** 12.5 GB

Proceed with download?

[[APPROVAL_NEEDED]]
```

After approval, execute:

```
[[DATA_CALL: consortium=encode, tool=download_files, accession=ENCSR000ABC, file_types=pod5]]
```

Report results:
- Number of files downloaded
- Total size
- Any failures
- Download location

### Step 4: Hand Off to Local Sample Intake

After download completes, **only `sample_name` and `input_directory` are known**. The remaining settings (sample type, reference genome, Dogme parameters) must still be collected from the user via `analyze_local_sample`, just like a local sample.

**State what's known and switch:**

```
✅ Files downloaded successfully to ./files/ENCSR000ABC/

- **Sample Name:** ENCSR000ABC
- **Data Path:** ./files/ENCSR000ABC/

I'll now collect the remaining pipeline settings (sample type, reference genome, etc.).

[[SKILL_SWITCH_TO: analyze_local_sample]]
```

The `analyze_local_sample` skill will see `sample_name` and `input_directory` (path) in the conversation history and only ask for the missing fields: `sample_type` and `reference_genome`.

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

## Notes

- Long-read experiments typically produce pod5 or fastq files
- Dogme pipeline handles basecalling, alignment, modification calling, and reporting
- Downloads are cached — re-running won't re-download existing files
- File organization: `./files/{accession}/`

## Important Rules

1. **Let the user choose** which file type to download (pod5, fastq, or bam) — do NOT auto-select.
2. **Only switch to `analyze_local_sample`** after files are downloaded. Never switch to `run_dogme_dna`, `run_dogme_rna`, or `run_dogme_cdna` — those are for post-job analysis only.
3. **State `sample_name` and data path explicitly** in your message before outputting `[[SKILL_SWITCH_TO: analyze_local_sample]]` so the intake skill can find them in conversation history.
4. **Do NOT collect sample type, reference genome, or Dogme settings** — that's `analyze_local_sample`'s job.
5. **Do NOT generate [[DATA_CALL: service=server3, ...]] tags** — job submission is handled automatically after user approval in the intake skill.
