# Skill: Remote SLURM Execution (`remote_execution`)

## Description

Guides the agent through collecting execution mode, SSH profile, SLURM resources, remote base path, result destination, stage-only requests, remote file browsing, and approval before submitting remote jobs. Uses staged prompting to minimize unnecessary questions by checking saved preferences first.

## Skill Scope & Routing

### ✅ This Skill Handles:
- Execution mode selection (local vs. SLURM)
- SSH profile selection and connection testing
- SLURM resource configuration (CPUs, memory, walltime, GPUs, partition, account)
- Remote path setup (work directory, output directory)
- Remote base path setup and browsing
- Stage-only remote sample preparation
- Reuse of already staged remote samples by name
- Result destination choice (keep remote, sync to local, both)
- Approval summary presentation before submission
- Run stage monitoring and status reporting
- Scheduler failure explanation and fix suggestions

**Example questions:**
- "Run this on the cluster"
- "Submit to SLURM with 32 CPUs"
- "Use my localCluster profile"
- "Stage Jamshid on localCluster"
- "Analyze Jamshid on localCluster"
- "List files on localCluster"
- "What's the status of my remote job?"
- "Why is my job still pending?"
- "Cancel the SLURM job"

### ❌ This Skill Does NOT Handle:

- **Analyzing job results** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
  - "Show me the output files"
  - "Parse the methylation BED file"
  - "What was the alignment rate?"

- **ENCODE data search** → `[[SKILL_SWITCH_TO: encode_search]]`
  - "Find ENCODE experiments for K562"
  - "How many BAM files for ENCSR160HKZ?"

- **Data download / file export** → `[[SKILL_SWITCH_TO: download_files]]`
  - "Download the results"
  - "Export to S3"

### 🔀 General Routing Rules:

**When the user's question is outside remote execution:**
- **Result analysis** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
- **ENCODE accessions/experiments** → `[[SKILL_SWITCH_TO: encode_search]]`
- **New local sample intake** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **General help / unclear intent** → `[[SKILL_SWITCH_TO: welcome]]`

**When uncertain:** If the question is clearly outside remote execution, switch to the appropriate skill rather than saying "I can't help."

## Staged Prompting Instructions

### Stage 1: Check Saved Preferences

Before asking any questions, use only available Launchpad tools to derive defaults:

1. List SSH profiles:
   ```
   [[DATA_CALL: service=launchpad, tool=list_ssh_profiles, user_id=<user_id>]]
   ```
2. If a profile is selected/known, fetch SLURM defaults:
   ```
   [[DATA_CALL: service=launchpad, tool=get_slurm_defaults, user_id=<user_id>, project_id=<project_id>]]
   ```

There is no `get_user_preferences` MCP tool. Do not call it.

Use these values as prefilled suggestions:
- Execution mode: infer from user intent (`slurm` or an explicit cluster/profile nickname implies SLURM; otherwise ask)
- SSH profile: use explicit nickname from user message when present; else ask user to pick from listed profiles
- Result destination: default to `local` unless user specifies `remote` or `both`
- Remote base path: use the saved profile `remote_base_path`

Only ask the user for values that have no saved preference.

### Stage 2: Execution Mode

Only ask for execution mode if `preferred_execution_mode` is not set:

> "Would you like to run this **locally** or on a **SLURM cluster**?"

If mode is `local`, hand off to normal local submission flow.

### Stage 3: SLURM Configuration (if mode is "slurm")

Collect or confirm the following in order:

1. **SSH Profile** — List available profiles; if `preferred_ssh_profile_id` is set, confirm it:
   ```
   [[DATA_CALL: service=launchpad, tool=list_ssh_profiles, user_id=<user_id>]]
   ```

2. **SLURM Resources** — Ask for or confirm with safe defaults:
   - Account and partition
   - CPUs (default: 4), memory (default: 16 GB), walltime (default: 04:00:00)
   - GPUs (default: 0)

3. **Remote Base Path** — Ask for or confirm the top-level folder used for remote staging and runs.
   - References stage under `{remote_base_path}/ref/{reference_id}`
   - Input data stages under `{remote_base_path}/data/{input_fingerprint}`
   - Runs stage under `{remote_base_path}/{project_slug}/workflowN`

4. **Result Destination** — if `preferred_result_destination` is not set:
   > "After completion, should results stay **remote**, be **synced to local**, or **both**?"

### Stage 4: Approval Summary

Present a summary of all collected details and ask for explicit approval before submission:

```
┌─────────────────────────────────────────────┐
│           Remote Job Submission Summary      │
├─────────────────────────────────────────────┤
│ Execution Mode:   SLURM                     │
│ SSH Profile:      localCluster (login.cluster.example.edu) │
│ Account:          lab_account                │
│ Partition:        standard                   │
│ CPUs:             16                         │
│ Memory:           64 GB                      │
│ Walltime:         12:00:00                   │
│ GPUs:             0                          │
│ Remote Base:      /remote/user/agoutic       │
│ Result Dest:      sync to local              │
└─────────────────────────────────────────────┘
```

> "Does this look correct? Type **yes** to submit or tell me what to change."

### Stage 5: Submission & Monitoring

After approval:

1. Validate SSH connection:
   ```
   [[DATA_CALL: service=launchpad, tool=test_ssh_connection, profile_id=<id>]]
   ```
2. If the user asked to browse files, call:
   ```
   [[DATA_CALL: service=launchpad, tool=list_remote_files, user_id=<user_id>, ssh_profile_id=<id>]]
   ```
3. If the user asked for stage-only, call:
   ```
   [[DATA_CALL: service=launchpad, tool=stage_remote_sample, project_id=<project_id>, user_id=<user_id>, sample_name=<sample>, mode=<mode>, input_directory=<path>, ssh_profile_id=<id>]]
   ```
4. Otherwise submit the job:
   ```
   [[DATA_CALL: service=launchpad, tool=submit_dogme_job, execution_mode=slurm, ...]]
   ```
5. Report stage transitions as they occur
6. If job enters PENDING: explain the pending reason using `explain_pending_reason`
7. If job fails: explain the failure using `explain_failure` and suggest fixes

## Available MCP Tools

| Tool | Purpose |
|------|---------|
| `submit_dogme_job` | Submit a job with `execution_mode` param (local/slurm) |
| `stage_remote_sample` | Stage data and references remotely without submitting a job |
| `list_remote_files` | Browse the configured remote base path or a subdirectory |
| `list_ssh_profiles` | List user's saved SSH profiles |
| `test_ssh_connection` | Test connectivity to an SSH profile |
| `get_slurm_defaults` | Retrieve default SLURM resources for a profile |
| `check_nextflow_status` | Poll Nextflow/SLURM job status |
| `cancel_slurm_job` | Cancel a running or queued SLURM job |

## Run Stage Labels

The following stages are reported to the user during execution:

| Stage | Label |
|-------|-------|
| `awaiting_details` | Awaiting details |
| `awaiting_approval` | Awaiting approval |
| `validating_connection` | Validating connection |
| `preparing_remote_dirs` | Preparing remote directories |
| `transferring_inputs` | Transferring inputs |
| `submitting_job` | Submitting job |
| `queued` | Queued on scheduler |
| `running` | Running |
| `collecting_outputs` | Collecting outputs |
| `syncing_results` | Syncing results to local |
| `completed` | Completed |
| `failed` | Failed |
| `cancelled` | Cancelled |

## Example Conversations

### Example 1: User with saved preferences

**User:** "Run Dogme DNA on my pod5 files at /data/experiment1"

*Agent checks preferences: execution_mode=slurm, ssh_profile_id=prof_123, result_destination=sync_local*

**Agent:** "I'll submit this to SLURM using your default localCluster profile. Here are the resource defaults:"
*(presents approval summary with defaults)*

**User:** "Looks good, but use 32 CPUs"

**Agent:** *(updates summary, re-presents, submits after approval)*

### Example 2: New user, no preferences

**User:** "I want to run this remotely"

**Agent:** "Would you like to run on a SLURM cluster? Let me check your SSH profiles..."
```
[[DATA_CALL: service=launchpad, tool=list_ssh_profiles, user_id=<user_id>]]
```

*(walks through each stage, collecting details one at a time)*

### Example 3: Job monitoring

**User:** "What's happening with my job?"

**Agent:** "Your job is currently in stage **queued** (Queued on scheduler). The SLURM state is PENDING with reason: *Resources* — waiting for resources to become available. This is normal for busy clusters; your job should start when nodes free up."

## KEY RULES

**DO:**
- Check saved preferences before asking questions
- Use only tools listed in this skill; never invent tool names
- Prefer `remote_base_path` from the saved profile; do not invent `/scratch/...` fallbacks
- For "Analyze Jamshid on localCluster", prefer reuse of an existing staged sample before restaging
- Present the approval summary before every submission
- Report stage transitions clearly with human-readable labels
- Explain scheduler states and pending reasons in plain language
- Suggest concrete fixes for failures (e.g., "Try increasing --mem" for OOM)
- Execute tool calls immediately — don't explain what you're about to do

**DON'T:**
- Ask for execution mode if a default is already saved
- Submit without explicit user approval
- Show raw SLURM state codes without human-readable explanations
- Explain your process step-by-step before executing
- Ask permission for obvious next steps like checking job status
