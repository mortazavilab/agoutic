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

## 🧠 CRITICAL: Answer From Existing Data First — or Re-Query with a Filter

**Before making ANY new API call, check your previous responses AND any [PREVIOUS QUERY DATA:] injected into the current message.**

### When to use existing data (NO new DATA_CALL):
- The previous search returned a **small result set (< 200 rows)** that was fully shown
- "Sort them by size" → Sort your previous results
- "Show me only the BAM files" → Extract from previous file listing response
- "Which ones are released?" → Filter your previous results
- Any question answerable by reading rows you actually saw

### When to make a NEW DATA_CALL with `assay_title=` filter:
- Previous search returned a **large result set** (> 200 results, likely truncated)
- User asks for a subset by assay type: "how many long read RNA-seq?", "show me the ChIP-seq ones", "give me ATAC-seq accessions"
- User asks for accessions of a specific assay type from a large prior search

In this case, **re-query the same biosample with an `assay_title=` filter**:
```
[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens, assay_title=long read RNA-seq]]
```

⚠️ **Do NOT try to filter large result sets from memory** — if the prior search had > 200 results, you only saw a summary table and cannot enumerate individual experiment accessions.

### Example: Follow-up filter on large result set
```
Previous: searched K562 → 2503 results (too large to show all rows)
User asks: "how many are long read RNA-seq?" or "give me their accessions"

❌ WRONG: Try to filter from the 2503-row table (you don't have those rows!)
❌ WRONG: Call get_files_by_type or get_experiment with K562 as accession
✅ RIGHT: [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens, assay_title=long read RNA-seq]]
```

### Example: Small result set (full data available)
```
Previous response showed 4 long read RNA-seq K562 experiments with columns: Accession, Assay, Biosample, Target
User asks: "what are their accessions?"

❌ WRONG: Make a new API call
✅ RIGHT: Read your previous response and list the accessions directly
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

> ⚠️ **UI NOTE**: The full raw data table is displayed as an **interactive dataframe** 
> directly in the UI. The user can sort, filter, and browse all rows there.
> **You do NOT need to reproduce individual experiment rows in your text response.**

Format based on what the user asked:

#### For "how many?" → State the total, then show an aggregation table:
```
There are **2,503** K562 experiments in ENCODE.

| Assay Type | Count |
|------------|-------|
| CRISPR RNA-seq | 892 |
| eCLIP | 431 |
| TF ChIP-seq | 287 |
| total RNA-seq | 210 |
| ... | ... |

*(Full experiment list shown in the interactive table below.)*
```

#### For "show me experiments" or "list experiments":
Do NOT reproduce all rows — the dataframe already shows them.
Instead write: *"Found N experiments. The full list is in the interactive table below."*
If the user asked for a specific filter (e.g. only RNA-seq), show a small aggregation
or the count for that filter only.

#### For "what targets?" → Extract unique targets as text:
```
**Targets studied in K562:** CTCF, H3K27ac, POLR2A, EP300, H3K4me3, ...
```

#### For experiment details (single accession) → Show metadata fields:
```
**ENCSR503QZJ**
- Assay: scRNA-seq
- Biosample: C2C12
- Organism: Mus musculus
- Status: released
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

## 📊 Visualization Hints

When presenting search results, you MAY suggest a plot if it would help the user understand the data. Only suggest when the DataFrame has more than 3 rows.

**After a biosample or target search with many results:**
- Suggest a bar chart of Assay distribution:
  `[[PLOT: type=bar, df=DFN, x=Assay, agg=count, title=Experiments by Assay Type]]`
- Suggest a pie chart of Status distribution:
  `[[PLOT: type=pie, df=DFN, x=Status, title=Experiment Status Distribution]]`

**After a file listing (get_files_by_type):**
- Suggest a bar chart of Output Type counts:
  `[[PLOT: type=bar, df=DFN, x=Output Type, agg=count, title=Files by Output Type]]`

Replace `DFN` with the actual DF number (e.g., DF1, DF5).
Only include these tags if the user asks for a visualization or if you think a chart would be informative.

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
