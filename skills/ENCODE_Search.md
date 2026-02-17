# Skill: ENCODE Portal Search & Browse

---
# ═══════════════════════════════════════════════════════════════
# 🚨 TAG FORMAT RULES - READ THIS FIRST 🚨
# ═══════════════════════════════════════════════════════════════

## MANDATORY TAG SYNTAX

**EVERY ENCODE query MUST use this EXACT format:**

```
[[DATA_CALL: consortium=encode, tool=tool_name, param1=value1, param2=value2]]
```

## ✅ CORRECT EXAMPLES (COPY THESE):

```
[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens]]
[[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR123ABC]]
```

## ❌ FORBIDDEN - NEVER WRITE THESE:

```
Get Experiment (accession=ENCSR123ABC)        ❌ NO! Missing [[brackets]]
Get Files By Type (accession=ENCSR123ABC)     ❌ NO! Missing [[brackets]]
[DATA_CALL: consortium=encode, tool=get_experiment...]              ❌ NO! Single brackets
[[get_experiment(accession=ENCSR123ABC)]]     ❌ NO! Function call syntax
```

**IF YOU WRITE "Get Experiment" OR "Get Files By Type" WITHOUT [[BRACKETS]], THE TOOL WILL NOT EXECUTE!**

---
## 🚨 YOU HAVE DIRECT ACCESS TO ENCODE DATA 🚨

**You can query the ENCODE Portal RIGHT NOW using the tags above.**
**DO NOT say "I cannot access" or "I would need to query" — you already have access!**
**For EVERY question about ENCODE data, write the [[DATA_CALL:...]] tag IMMEDIATELY.**

---

## 🧠 CRITICAL: Answer From Existing Data First

**Before making ANY new API call, check your previous responses AND any [PREVIOUS QUERY DATA:] injected into the current message.**

If you already fetched detailed data (file listings, experiment lists, search results), and the user asks a follow-up about that same data — **answer from what you already have.** Do NOT re-query.

### When to use existing data (NO new DATA_CALL):
- "Which of them are [output type]?" → Filter your previous file listing
- "Sort them by size" → Sort your previous results
- "Show me only the BAM files" → Extract from previous response
- "Which ones are released?" → Filter your previous results
- "What are the accessions for [subset]?" → Extract from previous search results
- "What are the long read RNA-seq accessions?" → Find them in the previous search table
- Any question that can be answered by reading your own previous response

### When to make a NEW DATA_CALL:
- User asks about a **different** accession or biosample not in previous data
- User asks for data you haven't fetched yet (e.g., detailed files after only doing a biosample search)
- User explicitly asks to refresh or re-query

### Example 1 — Filtering files:
```
Previous response showed 12 BAM files with columns: Accession, Output Type, Size, Status
User asks: "which of them are methylated reads?"

❌ WRONG: Make a new [[DATA_CALL:...]] — you already have the data!
✅ RIGHT: Read your previous response, filter for output_type="methylated reads", present those rows
```

### Example 2 — Extracting a subset from search results:
```
Previous response showed 58 C2C12 experiments with Accession, Assay, Biosample, Target columns
User asks: "what are the accessions for the long read RNA-seq samples?"

❌ WRONG: Make new API calls — you already have the complete list!
✅ RIGHT: Scan the previous table for rows where Assay="long read RNA-seq", list their accessions
```

**If [PREVIOUS QUERY DATA:] is injected at the start of your message, the answer is almost certainly in there. READ IT FIRST.**

---

## Description

This skill provides **DIRECT ACCESS** to the ENCODE Portal via Server 2 (ENCODELIB).

Use this skill to:
- Search for experiments by **biosample** (cell lines, tissues)
- Search for experiments by **target** (transcription factors, histone marks)
- Browse experiment metadata, file types, and summaries
- Create tables and summaries of ENCODE data
- Answer questions about what's available in ENCODE

## When to Use This Skill

- User mentions "ENCODE", "search ENCODE", "what experiments", "how many assays"
- User asks about cell lines (K562, C2C12, GM12878, HeLa, etc.)
- User asks about tissues
- User asks about targets (CTCF, H3K27ac, etc.)
- User asks about a specific accession, which have the form ENCXXYYYYYY, where XX is alphabetical and YY is alphanumeric
- User wants tables, counts, or summaries of ENCODE data

## When to Switch to Other Skills

- User wants to **download and process long-read data** → `[[SKILL_SWITCH_TO: ENCODE_LongRead]]`
- User mentions **local data** on disk → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- User asks about **completed job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`

## Approval Gates

**This skill does NOT need [[APPROVAL_NEEDED]] for anything.**

Searching, browsing, counting, making tables — these are all read-only operations.
Never trigger an approval gate from this skill.

If the user wants to download files, switch to ENCODE_LongRead:
```
[[SKILL_SWITCH_TO: ENCODE_LongRead]]
```

---

## How to Execute Queries

Write the tag on its own line. It executes automatically and returns data.

```
[[DATA_CALL: consortium=encode, tool=tool_name, param1=value1, param2=value2]]
```

---

## 🎯 KEY CONCEPT: Biosamples vs Targets

**BIOSAMPLES** = cell lines, tissues, organs, primary cells:
- K562, C2C12, GM12878, HeLa, HepG2, liver, brain, heart, etc.
- These are what the experiment was performed ON
- Use `search_by_biosample` with `search_term=`

**TARGETS** = proteins, transcription factors, histone modifications:
- CTCF, H3K27ac, H3K4me3, POLR2A, EP300, etc.
- These are what the experiment was measuring/targeting
- Use `search_by_target` with `target=`

**ASSAY TYPES** = the experimental technique:
- TF ChIP-seq, Histone ChIP-seq, RNA-seq, ATAC-seq, DNase-seq, etc.
- Use the `assay_title=` parameter to filter

**ORGANISMS**:
- Mouse cell lines (C2C12, etc.) → `organism=Mus musculus`
- Human cell lines (K562, GM12878, HeLa, etc.) → `organism=Homo sapiens`

---

## Quick Reference: User Query → Tag

| User Says | Tool & Parameters |
|-----------|-------------------|
| "C2C12 experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=C2C12, organism=Mus musculus]]` |
| "K562 experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens]]` |
| "K562 CTCF ChIP-seq" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, target=CTCF, assay_title=TF ChIP-seq, organism=Homo sapiens]]` |
| "All CTCF experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_target, target=CTCF, organism=Homo sapiens]]` |
| "Liver RNA-seq" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=liver, organism=Homo sapiens, assay_title=RNA-seq]]` |
| "Details for ENCSR123ABC" | `[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]` |
| "Files for ENCSR123ABC" | `[[DATA_CALL: consortium=encode, tool=get_file_types, accession=ENCSR123ABC]]` |
| "BAM files for ENCSR123ABC" | `[[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR123ABC]]` (then show result['bam']) |
| "Browse experiments" | `[[DATA_CALL: consortium=encode, tool=list_experiments, limit=50]]` |

---

## Available Tools & Parameters

**REMEMBER: ALL tools must be called using [[DATA_CALL: consortium=encode, tool=tool_name, params]] format!**

### Search Tools

**search_by_biosample:**
```
[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens]]
[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=C2C12, organism=Mus musculus, assay_title=TF ChIP-seq]]
[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=liver, target=CTCF, organism=Homo sapiens]]
```
Parameters:
- `search_term` (required): Cell line or tissue name
- `organism` (optional): "Homo sapiens" or "Mus musculus"
- `assay_title` (optional): Assay type filter
- `target` (optional): Target protein filter
- `exclude_revoked` (optional): default True

**search_by_target:**
```
[[DATA_CALL: consortium=encode, tool=search_by_target, target=CTCF, organism=Homo sapiens]]
[[DATA_CALL: consortium=encode, tool=search_by_target, target=H3K27ac, organism=Mus musculus]]
[[DATA_CALL: consortium=encode, tool=search_by_target, target=CTCF, assay_title=TF ChIP-seq, organism=Homo sapiens]]
```
Parameters:
- `target` (required): Target protein or histone mark
- `organism` (optional): "Homo sapiens" or "Mus musculus"
- `assay_title` (optional): Assay type filter
- `exclude_revoked` (optional): default True

**search_by_organism:**
```
[[DATA_CALL: consortium=encode, tool=search_by_organism, organism=Homo sapiens]]
[[DATA_CALL: consortium=encode, tool=search_by_organism, organism=Mus musculus, search_term=K562]]
```
Parameters:
- `organism` (required): Organism name
- `search_term` (optional): Biosample filter
- `assay_title` (optional): Assay type filter
- `target` (optional): Target filter
- `exclude_revoked` (optional): default True

### ⚠️ ENCODE Accession Types — Use the Right Tool!

| Prefix | Type | Example | Tool to use |
|--------|------|---------|-------------|
| ENCSR | Experiment | ENCSR160HKZ | `get_experiment` or `get_all_metadata` (param: `accession`) |
| ENCFF | File | ENCFF921XAH | `get_file_metadata` (param: `file_accession`) |

**NEVER change an accession prefix.** If the user says ENCFF921XAH, use ENCFF921XAH — do NOT change it to ENCSR921XAH.

### Metadata Tools

**get_experiment** (for ENCSR experiment accessions ONLY):
```
[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
```
Gets single experiment details. Parameter: `accession` (ENCSR...).

**get_all_metadata** (for ENCSR experiment accessions ONLY):
```
[[DATA_CALL: consortium=encode, tool=get_all_metadata, accession=ENCSR123ABC]]
```
Gets complete raw metadata for an experiment. Parameter: `accession` (ENCSR...).

**get_file_metadata** (for ENCFF file accessions — requires BOTH experiment and file accession):
```
[[DATA_CALL: consortium=encode, tool=get_file_metadata, accession=ENCSR123ABC, file_accession=ENCFF123ABC]]
```
Gets metadata for a specific file. Requires TWO parameters:
- `accession` — the parent experiment (ENCSR...)
- `file_accession` — the file itself (ENCFF...)

Find the experiment accession from the conversation history where the file was listed.

**get_file_types:**
```
[[DATA_CALL: consortium=encode, tool=get_file_types, accession=ENCSR123ABC]]
```
Lists available file types for an experiment.

**get_files_by_type:**
```
[[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR123ABC]]
```
Gets files organized by type (fastq, bam, tsv, gtf, etc.).
Returns: Dict with keys for each file type ('bam', 'fastq', 'bigWig', etc.), values are lists of file objects.

**To show only specific file types (e.g., BAM files):**
Call this tool and then present only the relevant section from the returned dict.
Example: After calling the tool, look at result['bam'] for BAM files, result['fastq'] for FASTQ files, etc.

**get_available_output_types:**
```
[[DATA_CALL: consortium=encode, tool=get_available_output_types, accession=ENCSR123ABC]]
```
Lists output types available for an experiment.

**get_files_summary:**
```
[[DATA_CALL: consortium=encode, tool=get_files_summary, accession=ENCSR123ABC, max_files_per_type=10]]
```
Gets file summary (limited files per type).

### Utility Tools

**list_experiments:**
```
[[DATA_CALL: consortium=encode, tool=list_experiments, limit=100, offset=0]]
```
Browse all experiments.

**get_cache_stats:**
```
[[DATA_CALL: consortium=encode, tool=get_cache_stats]]
```
Check cache status.

**get_server_info:**
```
[[DATA_CALL: consortium=encode, tool=get_server_info]]
```
Get server information.

---

## Plan Logic

# ═══════════════════════════════════════════════════════════════
# BEFORE YOU DO ANYTHING: CHECK YOUR TAG FORMAT!
# ═══════════════════════════════════════════════════════════════
# 
# ✅ CORRECT:   [[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
# ❌ WRONG:     Get Experiment (accession=ENCSR123ABC)
#
# If you write plain text without [[brackets]], the tool WILL NOT RUN!
# ═══════════════════════════════════════════════════════════════

### Step 1: Execute the Search

**IMMEDIATELY write the [[DATA_CALL:...]] tag as your first action.**

Choose the correct tool based on what the user mentioned:

- **Cell line/tissue name?** → `search_by_biosample`
- **Target/protein name?** → `search_by_target`
- **Just organism?** → `search_by_organism`
- **Accession number?** → `get_experiment`

Example:
```
I'll search ENCODE for C2C12 experiments:

[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=C2C12, organism=Mus musculus]]
```

**For multiple accessions (e.g., "get metadata for ENCSR123, ENCSR456, ENCSR789"):**

❌ **WRONG - DO NOT WRITE THIS:**
```
Get Experiment (accession=ENCSR123ABC)
Get Experiment (accession=ENCSR456DEF)
Get Experiment (accession=ENCSR789GHI)
```

✅ **CORRECT - WRITE THIS INSTEAD:**
```
I'll retrieve metadata for all three experiments:

[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]

[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR456DEF]]

[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR789GHI]]
```

**CRITICAL:** Each tag MUST have `[[` and `]]` around it. Plain text like "Get Experiment" will NOT execute!

### Step 2: Analyze and Present Results

After the tag executes, results appear automatically. **ANALYZE THEM IMMEDIATELY.**

**DO NOT ask "what would you like to see?" — just present the data!**

Format based on what the user asked:

#### For "how many assay types?" → Count and tabulate:
```
📊 **Assay Types for C2C12** (58 experiments total)

| Assay Type | Count |
|------------|-------|
| Histone ChIP-seq | 15 |
| TF ChIP-seq | 12 |
| scRNA-seq | 8 |
| RNA-seq | 6 |
| ATAC-seq | 5 |
| ... | ... |

**Total:** 58 experiments across 10 unique assay types
```

#### For "show me experiments" → List with key details:
```
| Accession | Assay | Biosample | Target | Status |
|-----------|-------|-----------|--------|--------|
| ENCSR503QZJ | scRNA-seq | C2C12 | — | released |
| ENCSR000AHP | Histone ChIP-seq | C2C12 | H3ac | released |
| ENCSR000AIK | TF ChIP-seq | C2C12 | FOSL1 | released |
```

#### For "what targets?" → Extract unique targets:
```
**Targets studied in C2C12:**
- H3ac, H3K79me2, FOSL1, H3K4me3, CTCF, ...
```

#### For experiment details → Show metadata:
```
**ENCSR503QZJ**
- Assay: scRNA-seq
- Biosample: C2C12
- Organism: Mus musculus
- Status: released
- Lab: ...
- Description: ...
```

### Step 3: Follow-up

If the user wants to:
- **See more details** → Use `get_experiment` or `get_all_metadata`
- **Check files** → Use `get_file_types` or `get_files_summary`
- **Get BAM/FASTQ/specific files** → Use `get_files_by_type`, then present the specific file type from the result
  - Result will be a dict: `{'bam': [...], 'fastq': [...], 'bigWig': [...], ...}`
  - To show only BAM files: present `result['bam']`
  - To show only FASTQ files: present `result['fastq']`
- **Download data** → Switch to ENCODE_LongRead: `[[SKILL_SWITCH_TO: ENCODE_LongRead]]`
- **Analyze local data** → Switch: `[[SKILL_SWITCH_TO: analyze_local_sample]]`

---

## Error Handling

### No Results
```
No ENCODE experiments found matching your query.

Suggestions:
- Check spelling of cell line/tissue name
- Try broader search (remove assay_title or target filter)
- Use list_experiments() to browse available data
```

### Empty Response
If a tool returns `{}` or `[]`, it means no matches. Tell the user and suggest alternatives.

---

## Notes

- All searches return real-time data from the ENCODE Portal
- Results are cached locally for fast repeat access
- Cell lines are case-sensitive in ENCODE (use "K562" not "k562")
- Common organisms: "Homo sapiens", "Mus musculus"

---

# ═══════════════════════════════════════════════════════════════════════════════
# 🚨 FINAL CHECKLIST - BEFORE SUBMITTING YOUR RESPONSE 🚨
# ═══════════════════════════════════════════════════════════════════════════════

**BEFORE you finish your response, verify:**

□ Did I write `[[DATA_CALL: consortium=encode, tool=...]]` with DOUBLE BRACKETS `[[` and `]]`?
□ Did I use commas between parameters (not parentheses)?
□ Did I avoid writing "Get Experiment" or "Get Files By Type" in plain text?
□ Will my tags actually EXECUTE, or will they be ignored?

**If you wrote this:** ❌
```
Get Experiment (accession=ENCSR123ABC)
```

**IMMEDIATELY fix it to this:** ✅
```
[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
```

**Remember:** Plain text descriptions will NOT execute. Only tags with [[double brackets]] work!

# ═══════════════════════════════════════════════════════════════════════════════
