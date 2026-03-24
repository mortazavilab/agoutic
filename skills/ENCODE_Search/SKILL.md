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

## ⛔ NEVER use `get_experiment` for biosample/count/assay queries

`get_experiment` requires exactly ONE param: `accession=ENCSR...`. It does NOT accept cell line names, assay filters, or any other search parameters.

```
❌ WRONG: [[DATA_CALL: consortium=encode, tool=get_experiment, assay_title=K562]]
❌ WRONG: [[DATA_CALL: consortium=encode, tool=get_experiment, accession=K562]]
❌ WRONG: [[DATA_CALL: consortium=encode, tool=get_experiment, accession=RNA-seq]]
❌ WRONG: [[DATA_CALL: consortium=encode, tool=get_experiment, assay_title=total RNA-seq]]

✅ Biosample query:       [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens]]
✅ Assay-only query:      [[DATA_CALL: consortium=encode, tool=search_by_assay, assay_title=total RNA-seq]]
✅ Specific experiment:   [[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
```

**Decision rule:**
- Has a gene name / gene symbol / Ensembl ID → `lookup_gene` (routed to edgePython)
- Has a cell line / tissue name → `search_by_biosample`
- Has only an assay type, no biosample → `search_by_assay`
- Has a specific ENCSR accession → `get_experiment`

## 🧬 Gene ID / Symbol Lookup (NOT an ENCODE tool)

If the user asks for a **gene ID**, **gene symbol**, or **gene annotation** (e.g. "what is the Ensembl ID for TP53?", "what gene is ENSG00000141510?"), this is **NOT an ENCODE search**. Use the gene lookup tool — it is automatically routed to edgePython:

```
[[DATA_CALL: service=edgepython, tool=lookup_gene, gene_symbols=["TP53"]]]
[[DATA_CALL: service=edgepython, tool=lookup_gene, gene_ids=["ENSG00000141510"]]]
```

⛔ NEVER use `get_experiment`, `search_by_biosample`, or any ENCODE tool for gene lookups.
⛔ NEVER use invented tools like `get_gene_info` or `gene_info` — they do not exist.
✅ ALWAYS use `lookup_gene` with `service=edgepython` for gene queries.

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
## 🔀 DOWNLOAD ROUTING

If the user asks to **download** specific files (e.g. "download ENCFF546HTC and ENCFF212RUB"), do NOT handle it here.
Instead, **immediately** switch to the download skill:

```
[[SKILL_SWITCH_TO: download_files]]
```

Signals: "download", "grab", "fetch", "save" + ENCFF accessions.

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
Previous response showed 58 C2C12 experiments with columns: Accession, Assay, Biosample, Target
User asks: "what are the accessions for the long read RNA-seq samples?"

❌ WRONG: Make a new API call (get_files_by_type, get_experiment, etc.)
❌ WRONG: Return ENCFF file accessions — those are files, not experiments
✅ RIGHT: Read your previous response, filter to "long read RNA-seq" assay, list the ENCSR experiment accessions
```

**If [PREVIOUS QUERY DATA:] is injected at the start of your message, the answer is almost certainly in there. READ IT FIRST.**

---

## Description

This skill provides **DIRECT ACCESS** to the ENCODE Portal via Atlas (ENCODELIB).

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

## 🎯 KEY CONCEPT: Biosamples vs Targets vs Assays

**BIOSAMPLES** = cell lines, tissues, organs, primary cells:
- **Human cell lines:** K562, GM12878, HeLa, HepG2, HEK293, A549, MCF-7, Jurkat, IMR-90, U2OS, H1, H9, HFF, WTC-11, LNCAP, PANC-1, SK-N-SH
- **Mouse cell lines:** C2C12, NIH3T3, MEF, MEL, ES-E14, mESC, V6.5, G1E, G1E-ER4, CH12.LX
- **Primary cells / tissues:** liver, brain, heart, lung, kidney, spleen, thymus, pancreas, adrenal gland, endothelial cell, fibroblast, monocyte, T-cell, B-cell, neural progenitor, iPSC, organoid
- These are what the experiment was performed ON
- Use `search_by_biosample` with `search_term=`

**TARGETS** = proteins, transcription factors, histone modifications:
- **Transcription factors:** CTCF, POLR2A, EP300, MAX, MYC, JUN, FOS, REST, YY1, TCF7L2, GATA1, GATA2, SPI1, CEBPB, STAT1, STAT3, IRF1, NRF1
- **Histone marks:** H3K27ac, H3K4me3, H3K4me1, H3K36me3, H3K27me3, H3K9me3, H3K79me2, H2AFZ, H4K20me1
- **Cohesin / chromatin:** SMC3, RAD21, NIPBL
- These are what the experiment was measuring/targeting
- Use `search_by_target` with `target=`

**ASSAY TYPES** = the experimental technique:
- TF ChIP-seq, Histone ChIP-seq, total RNA-seq, long read RNA-seq, polyA plus RNA-seq, ATAC-seq, DNase-seq, WGBS, RRBS, eCLIP, in situ Hi-C, microRNA-seq, RAMPAGE, CAGE, PRO-seq, GRO-seq, Mint-ChIP-seq, CRISPR RNA-seq, shRNA RNA-seq
- Use the `assay_title=` parameter to filter

**ORGANISMS**:
- Mouse cell lines (C2C12, NIH3T3, etc.) → `organism=Mus musculus`
- Human cell lines (K562, GM12878, HeLa, etc.) → `organism=Homo sapiens`

---

## ⚠️ IMPORTANT: Don't Limit Yourself to Known Terms

**You do NOT need to recognise a biosample name to query it.** The ENCODE API accepts any biosample term — if it doesn't exist, the API simply returns zero results. That's fine.

**Default rule:** If the user mentions a term that isn't clearly an assay type or a target protein, **treat it as a biosample** and use `search_by_biosample`. Examples:
- "How many MEL experiments?" → `search_by_biosample` with `search_term=MEL`
- "Search ENCODE for Panc1" → `search_by_biosample` with `search_term=Panc1`
- "Any WTC-11 data?" → `search_by_biosample` with `search_term=WTC-11`

**Only use a different tool when you're SURE it's not a biosample:**
- **Target clues:** protein names are often uppercase abbreviations (CTCF, POLR2A), histone marks follow H3K/H4K patterns (H3K27ac, H3K4me3) → use `search_by_target`
- **Assay clues:** technique names contain "-seq", "ChIP", "CLIP", "Hi-C", method suffixes → use `search_by_assay`

**When truly ambiguous** (e.g., user says "search ENCODE for XYZ" and XYZ could be a protein or a cell line), ask:
```
Is "{term}" a cell line/tissue (biosample) or a protein/histone mark (target)? That determines which search I run.
```

🚨 **NEVER invent a count.** If you're not sure, QUERY first — don't guess "1,000" or any other number.

---

## Quick Reference: User Query → Tag

| User Says | Tool & Parameters |
|-----------|-------------------|
| "C2C12 experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=C2C12, organism=Mus musculus]]` |
| "K562 experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens]]` |
| "GM12878 experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=GM12878, organism=Homo sapiens]]` |
| "How many GM12878 experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=GM12878, organism=Homo sapiens]]` |
| "K562 CTCF ChIP-seq" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, target=CTCF, assay_title=TF ChIP-seq, organism=Homo sapiens]]` |
| "All CTCF experiments" | `[[DATA_CALL: consortium=encode, tool=search_by_target, target=CTCF, organism=Homo sapiens]]` |
| "Liver RNA-seq" | `[[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=liver, organism=Homo sapiens, assay_title=total RNA-seq]]` |
| **"How many RNA-seq experiments"** | `[[DATA_CALL: consortium=encode, tool=search_by_assay, assay_title=total RNA-seq]]` |
| **"How many ATAC-seq are in ENCODE"** | `[[DATA_CALL: consortium=encode, tool=search_by_assay, assay_title=ATAC-seq]]` |
| **"How many ChIP-seq experiments"** | `[[DATA_CALL: consortium=encode, tool=search_by_assay, assay_title=TF ChIP-seq]]` |
| "Details for ENCSR123ABC" | `[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]` |
| "Files for ENCSR123ABC" | `[[DATA_CALL: consortium=encode, tool=get_file_types, accession=ENCSR123ABC]]` |
| "BAM files for ENCSR123ABC" | `[[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR123ABC]]` (then show result['bam']) |
| "Browse experiments" | `[[DATA_CALL: consortium=encode, tool=list_experiments, limit=50]]` |

**Key rule: no biosample → `search_by_organism`. Has biosample → `search_by_biosample`.**

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

**search_by_assay** ← USE THIS for assay-type queries with no specific biosample:
```
[[DATA_CALL: consortium=encode, tool=search_by_assay, assay_title=total RNA-seq]]
[[DATA_CALL: consortium=encode, tool=search_by_assay, assay_title=ATAC-seq, organism=Homo sapiens]]
[[DATA_CALL: consortium=encode, tool=search_by_assay, assay_title=TF ChIP-seq, target=CTCF]]
```
Parameters:
- `assay_title` (required): Assay type (e.g. 'total RNA-seq', 'ATAC-seq', 'TF ChIP-seq')
- `organism` (optional): 'Homo sapiens' or 'Mus musculus'. If omitted, both are queried and counts are returned for each.
- `target` (optional): Target filter
- `exclude_revoked` (optional): default True

Returns `total`, `human_count`, `mouse_count` (when organism omitted) or `total` + `experiments` list.

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
# 🚨🚨🚨 DO NOT DESCRIBE STEPS — EXECUTE IMMEDIATELY 🚨🚨🚨
# ═══════════════════════════════════════════════════════════════
#
# ❌ DO NOT write: "STEP 1: Search ENCODE for..."
# ❌ DO NOT write: "STEP 2: Count the results..."
# ❌ DO NOT write: "Let me perform a search..."
# ❌ DO NOT write: "I will use the search function..."
# ❌ DO NOT write: Search Encode (biosample=GM12878)
#
# ✅ JUST WRITE THE TAG:
# [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=GM12878, organism=Homo sapiens]]
#
# ✅ CORRECT:   [[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
# ❌ WRONG:     Get Experiment (accession=ENCSR123ABC)
# ❌ WRONG:     Search Encode (biosample=GM12878)
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

## Plan Chains

### search_and_visualize
- description: Search ENCODE and visualize the results
- trigger: search|get|find|query|show|plot|chart|visualize|graph|make + plot|chart|visualize|graph|pie|histogram|bar|scatter|heatmap|distribution|by
- steps:
  1. SEARCH_DATA: Search ENCODE for the requested data
  2. GENERATE_PLOT: Visualize the results
- auto_approve: true
- plot_hint: Infer chart type and grouping column from the user's message

### visualize_existing
- description: Plot data from a previous ENCODE query
- trigger: plot|chart|visualize|graph|pie|histogram|bar|scatter|heatmap|distribution + assay|biosample|target|type|status|organism + this|it|the data|results|previous|existing|DF
- steps:
  1. GENERATE_PLOT: Plot the existing data by the requested dimension
- auto_approve: true
- plot_hint: Use the most recent DataFrame from the conversation

### search_filter_visualize
- description: Search ENCODE, apply filters, and visualize
- trigger: search|get|find + filter|only|just|specific|subset + plot|chart|visualize|graph
- steps:
  1. SEARCH_DATA: Search ENCODE for the requested data
  2. FILTER_DATA: Apply the requested filters
  3. GENERATE_PLOT: Plot the filtered results
- auto_approve: true
- plot_hint: Infer filters and chart type from the user's message
