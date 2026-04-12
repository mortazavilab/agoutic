# Skill: IGVF Portal Search & Browse

---
# ═══════════════════════════════════════════════════════════════
# 🚨 TAG FORMAT RULES - READ THIS FIRST 🚨
# ═══════════════════════════════════════════════════════════════

## MANDATORY TAG SYNTAX

**EVERY IGVF query MUST use this EXACT format:**

```
[[DATA_CALL: consortium=igvf, tool=tool_name, param1=value1, param2=value2]]
```

## ⛔ NEVER use `get_dataset` for sample/count/assay queries

`get_dataset` requires exactly ONE param: `accession=IGVFDS...`. It does NOT accept cell line names, assay filters, or any other search parameters.

```
❌ WRONG: [[DATA_CALL: consortium=igvf, tool=get_dataset, assay=ATAC-seq]]
❌ WRONG: [[DATA_CALL: consortium=igvf, tool=get_dataset, accession=K562]]

✅ Sample query:          [[DATA_CALL: consortium=igvf, tool=search_by_sample, sample_term=K562, organism=Homo sapiens]]
✅ Assay-only query:      [[DATA_CALL: consortium=igvf, tool=search_by_assay, assay_title=ATAC-seq]]
✅ Specific dataset:      [[DATA_CALL: consortium=igvf, tool=get_dataset, accession=IGVFDS1234ABCD]]
```

**Decision rule:**
- Has a cell line / tissue name → `search_by_sample`
- Has only an assay type, no sample → `search_by_assay`
- Has a specific IGVFDS accession → `get_dataset`
- Wants to browse datasets → `search_measurement_sets`
- Wants analysis data → `search_analysis_sets`
- Wants predictions → `search_prediction_sets`
- Wants gene info → `search_genes` or `get_gene`
- Wants file info → `search_files`, `get_file_metadata`, or `get_files_for_dataset`

## ✅ CORRECT EXAMPLES (COPY THESE):

```
[[DATA_CALL: consortium=igvf, tool=get_dataset, accession=IGVFDS1234ABCD]]
[[DATA_CALL: consortium=igvf, tool=search_by_sample, sample_term=K562, organism=Homo sapiens]]
[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, assay=ATAC-seq, limit=50]]
```

## ❌ FORBIDDEN - NEVER WRITE THESE:

```
Get Dataset (accession=IGVFDS1234ABCD)        ❌ NO! Missing [[brackets]]
Search IGVF (sample=K562)                     ❌ NO! Missing [[brackets]]
[DATA_CALL: consortium=igvf, tool=...]         ❌ NO! Single brackets
[[get_dataset(accession=IGVFDS1234ABCD)]]      ❌ NO! Function call syntax
```

**IF YOU WRITE TOOL NAMES WITHOUT [[BRACKETS]], THE TOOL WILL NOT EXECUTE!**

---
## 🚨 YOU HAVE DIRECT ACCESS TO IGVF DATA 🚨

**You can query the IGVF Data Portal RIGHT NOW using the tags above.**
**DO NOT say "I cannot access" or "I would need to query" — you already have access!**
**For EVERY question about IGVF data, write the [[DATA_CALL:...]] tag IMMEDIATELY.**

---

## Description

This skill provides **DIRECT ACCESS** to the IGVF Data Portal (https://data.igvf.org).

Use this skill to:
- Search for MeasurementSets, AnalysisSets, and PredictionSets
- Browse files, genes, and samples
- Get dataset metadata and file download URLs
- Create tables and summaries of IGVF data

## When to Use This Skill

- User mentions "IGVF", "search IGVF", "IGVF portal"
- User asks about IGVF-specific accessions (IGVFDS..., IGVFFI...)
- User asks about MeasurementSets, AnalysisSets, or PredictionSets
- User wants to search the IGVF data portal

## When to Switch to Other Skills

- User asks about **ENCODE data** → `[[SKILL_SWITCH_TO: ENCODE_Search]]`
- User mentions **local data** on disk → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- User wants to **download IGVF files** to their project → `[[SKILL_SWITCH_TO: download_files]]`

## Approval Gates

**This skill does NOT need [[APPROVAL_NEEDED]] for anything.**
Searching, browsing, counting, making tables — these are all read-only operations.

---

## 🎯 KEY CONCEPTS: IGVF Object Types

### Dataset Types (IGVFDS accessions)

**MeasurementSets** — Primary experimental data (equivalent to ENCODE Experiments):
- Represent assay data produced from biological samples
- Have assay terms, samples, lab info, files
- Use `search_measurement_sets` or `search_by_sample`/`search_by_assay`

**AnalysisSets** — Computational analysis results:
- Derived from MeasurementSets via computational pipelines
- Use `search_analysis_sets`

**PredictionSets** — Computational predictions:
- Model predictions (variant effects, regulatory elements, etc.)
- Use `search_prediction_sets`

### Files (IGVFFI accessions)

Files belong to datasets. Common formats: fastq, bam, bed, tsv, vcf, bigWig.
- Use `search_files` for general file search
- Use `get_files_for_dataset` to get files for a specific dataset
- Use `get_file_metadata` for single file details
- Use `get_file_download_url` for download links

### Genes and Samples

- Use `search_genes` / `get_gene` for gene lookups
- Use `search_samples` for browsing biosamples

### ⚠️ IGVF Accession Types — Use the Right Tool!

| Prefix | Type | Example | Tool to use |
|--------|------|---------|-------------|
| IGVFDS | Dataset | IGVFDS1234ABCD | `get_dataset` (param: `accession`) |
| IGVFFI | File | IGVFFI1234ABCD | `get_file_metadata` (param: `file_accession`) |

**NEVER change an accession prefix.** If the user says IGVFFI..., use `get_file_metadata`.
If the user says IGVFDS..., use `get_dataset`.

---

## Quick Reference: User Query → Tag

| User Says | Tool & Parameters |
|-----------|-------------------|
| "IGVF ATAC-seq datasets" | `[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, assay=ATAC-seq]]` |
| "K562 data in IGVF" | `[[DATA_CALL: consortium=igvf, tool=search_by_sample, sample_term=K562, organism=Homo sapiens]]` |
| "IGVF whole genome sequencing" | `[[DATA_CALL: consortium=igvf, tool=search_by_assay, assay_title=whole genome sequencing]]` |
| "Details for IGVFDS1234ABCD" | `[[DATA_CALL: consortium=igvf, tool=get_dataset, accession=IGVFDS1234ABCD]]` |
| "Files for IGVFDS1234ABCD" | `[[DATA_CALL: consortium=igvf, tool=get_files_for_dataset, accession=IGVFDS1234ABCD]]` |
| "IGVF analysis sets" | `[[DATA_CALL: consortium=igvf, tool=search_analysis_sets, limit=50]]` |
| "IGVF prediction sets" | `[[DATA_CALL: consortium=igvf, tool=search_prediction_sets]]` |
| "Search IGVF for TP53" | `[[DATA_CALL: consortium=igvf, tool=search_genes, query=TP53]]` |
| "IGVF samples from human" | `[[DATA_CALL: consortium=igvf, tool=search_samples, organism=Homo sapiens]]` |
| "BAM files in IGVF" | `[[DATA_CALL: consortium=igvf, tool=search_files, file_format=bam]]` |
| "Browse IGVF datasets" | `[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, limit=50]]` |

---

## Available Tools & Parameters

### Search Tools

**search_measurement_sets:**
```
[[DATA_CALL: consortium=igvf, tool=search_measurement_sets, assay=ATAC-seq, organism=Homo sapiens, limit=50]]
```
Parameters:
- `status` (optional): 'released' (default), 'in progress', 'archived', 'revoked'
- `assay` (optional): Assay term name
- `sample` (optional): Sample summary filter
- `organism` (optional): 'Homo sapiens' or 'Mus musculus'
- `lab` (optional): Lab name filter
- `limit` (optional): Max results (default 25)

**search_analysis_sets:**
```
[[DATA_CALL: consortium=igvf, tool=search_analysis_sets, organism=Homo sapiens]]
```
Parameters: `status`, `sample`, `organism`, `limit`

**search_prediction_sets:**
```
[[DATA_CALL: consortium=igvf, tool=search_prediction_sets, organism=Homo sapiens]]
```
Parameters: `status`, `sample`, `organism`, `limit`

**search_by_sample:**
```
[[DATA_CALL: consortium=igvf, tool=search_by_sample, sample_term=K562, organism=Homo sapiens]]
```
Parameters:
- `sample_term` (required): Sample name (e.g. 'K562', 'liver', 'iPSC')
- `organism` (optional)
- `assay` (optional)
- `status` (optional)
- `limit` (optional)

**search_by_assay:**
```
[[DATA_CALL: consortium=igvf, tool=search_by_assay, assay_title=ATAC-seq]]
```
Parameters:
- `assay_title` (required): Assay type
- `organism` (optional)
- `status` (optional)
- `limit` (optional)

**search_files:**
```
[[DATA_CALL: consortium=igvf, tool=search_files, file_format=bam, limit=25]]
```
Parameters:
- `file_format` (optional): 'fastq', 'bam', 'bed', 'tsv', 'vcf', etc.
- `content_type` (optional): 'reads', 'alignments', 'variant calls', etc.
- `dataset_accession` (optional): Filter by parent dataset
- `status` (optional)
- `limit` (optional)

### Object Retrieval Tools

**get_dataset:**
```
[[DATA_CALL: consortium=igvf, tool=get_dataset, accession=IGVFDS1234ABCD]]
```
Get full metadata for a dataset. Parameter: `accession` (IGVFDS format).

**get_file_metadata:**
```
[[DATA_CALL: consortium=igvf, tool=get_file_metadata, file_accession=IGVFFI1234ABCD]]
```
Get metadata for a specific file. Parameter: `file_accession` (IGVFFI format).

**get_file_download_url:**
```
[[DATA_CALL: consortium=igvf, tool=get_file_download_url, file_accession=IGVFFI1234ABCD]]
```
Get download URL for a file.

**get_files_for_dataset:**
```
[[DATA_CALL: consortium=igvf, tool=get_files_for_dataset, accession=IGVFDS1234ABCD]]
[[DATA_CALL: consortium=igvf, tool=get_files_for_dataset, accession=IGVFDS1234ABCD, file_format=bam]]
```
Get all files for a dataset, optionally filtered by format.

### Gene & Sample Tools

**search_genes:**
```
[[DATA_CALL: consortium=igvf, tool=search_genes, query=TP53, organism=Homo sapiens]]
```

**get_gene:**
```
[[DATA_CALL: consortium=igvf, tool=get_gene, gene_id=TP53]]
```

**search_samples:**
```
[[DATA_CALL: consortium=igvf, tool=search_samples, organism=Homo sapiens, sample_type=InVitroSystem]]
```

### Utility Tools

**get_server_info:**
```
[[DATA_CALL: consortium=igvf, tool=get_server_info]]
```

---

## Plan Logic

# ═══════════════════════════════════════════════════════════════
# 🚨🚨🚨 DO NOT DESCRIBE STEPS — EXECUTE IMMEDIATELY 🚨🚨🚨
# ═══════════════════════════════════════════════════════════════
#
# ❌ DO NOT write: "STEP 1: Search IGVF for..."
# ❌ DO NOT write: "Let me perform a search..."
#
# ✅ JUST WRITE THE TAG:
# [[DATA_CALL: consortium=igvf, tool=search_measurement_sets, assay=ATAC-seq]]
#
# If you write plain text without [[brackets]], the tool WILL NOT RUN!
# ═══════════════════════════════════════════════════════════════

### Step 1: Execute the Search

**IMMEDIATELY write the [[DATA_CALL:...]] tag as your first action.**

Choose the correct tool based on what the user mentioned:

- **Cell line/tissue name?** → `search_by_sample`
- **Assay type only?** → `search_by_assay`
- **Browse datasets?** → `search_measurement_sets`
- **IGVFDS accession?** → `get_dataset`
- **IGVFFI accession?** → `get_file_metadata`
- **Gene name?** → `search_genes`

### Step 2: Analyze and Present Results

After the tag executes, results appear automatically. Present the data.

- For "how many?" → State the total count, then show aggregation
- For "show me datasets" → Reference the interactive table
- For dataset details → Show key metadata fields

---

## Error Handling

### No Results
```
No IGVF datasets found matching your query.

Suggestions:
- Check spelling of sample/assay name
- Try broader search (remove organism or status filters)
- Use search_measurement_sets with fewer filters to browse
```

---

## Notes

- All searches return real-time data from the IGVF Data Portal (data.igvf.org)
- Results are cached locally for fast repeat access (5 minute TTL)
- IGVF is a Snovault-based portal (same framework as ENCODE)
- Common accession prefixes: IGVFDS (datasets), IGVFFI (files)

---

# ═══════════════════════════════════════════════════════════════════════════════
# 🚨 FINAL CHECKLIST - BEFORE SUBMITTING YOUR RESPONSE 🚨
# ═══════════════════════════════════════════════════════════════════════════════

**BEFORE you finish your response, verify:**

□ Did I write `[[DATA_CALL: consortium=igvf, tool=...]]` with DOUBLE BRACKETS `[[` and `]]`?
□ Did I use commas between parameters (not parentheses)?
□ Did I avoid writing tool names in plain text without brackets?
□ Will my tags actually EXECUTE, or will they be ignored?

# ═══════════════════════════════════════════════════════════════════════════════
