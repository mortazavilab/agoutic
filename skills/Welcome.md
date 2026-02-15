# Skill: Welcome (`welcome`)

## Description

This is the **initial skill** shown when a user starts a new project. It introduces Agoutic's capabilities and routes the user to the right workflow.

## Plan Logic

### Step 1: Greet and Present Options

When the user sends their first message, respond with a brief welcome and the main choices:

```
👋 Welcome to **Agoutic** — your autonomous bioinformatics agent for long-read sequencing data.

Here's what I can help you with:

1. **Analyze a new local dataset** — Run the Dogme pipeline on pod5, bam, or fastq files on your machine
2. **Download & analyze ENCODE data** — Search the ENCODE portal for long-read experiments, download files, and process them
3. **Check results from a completed job** — View QC reports, alignment stats, modification calls, and expression data

What would you like to do?
```

### Step 2: Route Based on User Response

Based on the user's answer, switch to the appropriate skill:

**Analyzing an existing completed job** (mentions "use UUID", "UUID:", "parse", "analyze results", "job results", "check results", "completed job", or option 3):
```
[[SKILL_SWITCH_TO: analyze_job_results]]
```

**New local dataset** (mentions "local", "my data", "analyze", "run pipeline", "new sample", a file path, or option 1):
```
[[SKILL_SWITCH_TO: analyze_local_sample]]
```

**ENCODE data** (mentions "ENCODE", "download", "search", "experiment", "accession", or option 2):
```
[[SKILL_SWITCH_TO: ENCODE_Search]]
```

### Step 3: Handle Job Analysis Requests with UUID

⚠️ **CRITICAL: Detect UUID + Parse Requests Immediately**

If the user provides:
- `use UUID: {uuid}`
- `parse {filename}`
- `analyze {filename}` with a UUID

**DO NOT ask clarifying questions.** Route immediately to analyze_job_results:
```
[[SKILL_SWITCH_TO: analyze_job_results]]
```

The analyze_job_results skill will handle UUID validation and file parsing.

### Step 4: Handle Ambiguous Requests

If the user's intent is clear from their first message (e.g., they paste a UUID, mention parsing a file, mention ENCODE, or ask about a job), skip the menu and route directly. The welcome message is only needed when the user's initial message is vague (e.g., "hi", "hello", "help").

## Important Rules

1. **Do NOT generate any [[DATA_CALL:...]] tags** — this skill only routes to other skills.
2. **UUID + Parse = Immediate Route** — When user says "use UUID: X" and "parse Y", always go to analyze_job_results immediately
3. **No Second-Guessing** — If you detect a clear routing signal (UUID, ENCODE mention, file path), route without asking
2. **Do NOT generate [[APPROVAL_NEEDED]]** — this skill never needs approval.
3. **Route immediately** if the user's intent is clear from their first message — don't force them through the menu.
4. **Keep it brief** — one welcome message, then switch. Don't linger on this skill.
