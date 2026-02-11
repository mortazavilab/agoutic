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
- User asks about targets (CTCF, H3K27ac, etc.)
- User wants tables, counts, or summaries of ENCODE data
- Default skill for new conversations

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

### Metadata Tools

**get_experiment:**
```
[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
```
Gets single experiment details.

**get_all_metadata:**
```
[[DATA_CALL: consortium=encode, tool=get_all_metadata, accession=ENCSR123ABC]]
```
Gets complete raw metadata.

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
