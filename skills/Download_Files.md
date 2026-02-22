# Skill: Download Files (`download_files`)

## Description

This skill handles **downloading files** into the user's project from two sources:
1. **ENCODE files** — given an experiment accession and file type, resolve URLs via Atlas and download.
2. **Arbitrary URLs** — direct HTTP/HTTPS links to files.

Files are saved into `AGOUTIC_DATA/users/{username}/{project_slug}/data/` (or a user-specified subfolder).

After downloads complete, the skill suggests next steps based on file types.

## Skill Scope & Routing

### ✅ This Skill Handles:
- "Download these files"
- "Download the pod5 files from ENCSR000ABC"
- "Grab these URLs for me"
- "Save this file to my project"
- User pastes one or more HTTP/HTTPS URLs

### ❌ This Skill Does NOT Handle:
- **Searching or browsing ENCODE** → `[[SKILL_SWITCH_TO: ENCODE_Search]]`
  - Example: "How many experiments are there for K562?"
  - Example: "Show me mouse RNA-seq experiments"
- **Running the Dogme pipeline** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
  - Example: "Run Dogme on my data"
- **Analyzing completed job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
  - Example: "Show me the QC report"
- **General help** → `[[SKILL_SWITCH_TO: welcome]]`
  - Example: "What can you do?"

## Atlas Tools Available (ENCODE only)

Use these [[DATA_CALL: consortium=encode, tool=...]] tags to resolve ENCODE file URLs:

```
get_experiment(accession="ENCSR123ABC")
get_files_summary(accession="ENCSR123ABC")
get_file_url(accession="ENCSR123ABC", file_accession="ENCFF123ABC")
```

**Important:** Do NOT use `download_files` from Atlas. Instead, resolve URLs with `get_file_url` and then use the project download endpoint.

## Plan Logic

### Step 1: Determine Source

Look at what the user asked:
- If they mention an ENCODE accession → ENCODE source
- If they paste URLs → URL source
- If unclear, ask

### Step 2a: ENCODE Files

If the user wants ENCODE files:

1. Fetch the experiment details:
```
[[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR000ABC]]
```

2. Get the available files:
```
[[DATA_CALL: consortium=encode, tool=get_files_summary, accession=ENCSR000ABC]]
```

3. Present the available file types and let the user choose (pod5, fastq, bam, etc.)

4. For each chosen file, resolve the download URL:
```
[[DATA_CALL: consortium=encode, tool=get_file_url, accession=ENCSR000ABC, file_accession=ENCFF456DEF]]
```

5. Present the download plan with file list, sizes, and destination:

```
**Download Plan:**
- **Source:** ENCODE experiment ENCSR000ABC
- **Files:** 4 pod5 files (12.5 GB total)
- **Destination:** data/

Proceed with download?

[[APPROVAL_NEEDED]]
```

6. After approval, the system downloads files via the project download endpoint.

### Step 2b: URL Downloads

If the user provides URLs:

1. List the URLs and estimated file names.
2. Present the download plan:

```
**Download Plan:**
- **Files:** 3 files from provided URLs
- **Destination:** data/

Proceed with download?

[[APPROVAL_NEEDED]]
```

3. After approval, the system downloads files via the project download endpoint.

### Step 3: Post-Download Suggestions

After download completes, check the file types and suggest next steps:

**Sequencing data** (.pod5, .bam, .fastq, .fast5):
```
These look like sequencing data. I can set up a Dogme pipeline run for DNA, RNA, or cDNA analysis.
Would you like to proceed?

[[SKILL_SWITCH_TO: analyze_local_sample]]
```

**Tabular data** (.csv, .tsv, .bed, .bedgraph):
```
I can load these into a dataframe for exploration and analysis.
Would you like me to read and summarize them?

[[SKILL_SWITCH_TO: analyze_job_results]]
```

**Other files:**
```
Files downloaded successfully. Let me know what you'd like to do with them.
```

## Error Handling

### No URLs Provided
```
I didn't find any URLs in your message. Please provide:
- An ENCODE accession (e.g., ENCSR000ABC) with a file type
- Or one or more direct download URLs
```

### Download Failures
```
⚠️ Some files failed to download.

Failed: {failed_list}

Options:
1. Retry the failed downloads
2. Continue with the successfully downloaded files
```

### No Username Set
```
You need to set a username before downloading files. Please set one in your profile settings.
```

## Important Rules

1. **Always require approval** before starting downloads — use `[[APPROVAL_NEEDED]]`.
2. **Use get_file_url from Atlas** to resolve ENCODE file URLs — do NOT use Atlas's download_files tool.
3. **Let the user choose** which file types to download — don't auto-select.
4. **Show file sizes** whenever available so the user knows what to expect.
5. **After download, suggest next steps** based on file types (Dogme, dataframes, etc.).
6. **Files go into data/** by default under the user's project directory.
