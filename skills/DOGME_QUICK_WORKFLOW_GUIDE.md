# Dogme Quick Workflow Guide - Shared Instructions

This document contains the consolidated workflow steps for parsing files across all Dogme analysis skills (DNA, RNA, cDNA). Each mode-specific skill references this guide.

## UUID Guidance - Critical Foundation

⚠️ **🚨 CRITICAL - DO THIS FIRST: Get and Verify the UUID 🚨**

**The #1 cause of "File not found" errors is using the WRONG UUID.**

**ACTION:** Before making ANY tool calls, look at the conversation for the MOST RECENT "📊 Analysis Ready" message. Extract the UUID from that message.

**WRONG:** Using a UUID from an earlier section of the conversation
- ❌ `6a8613d4-832c-4420-927e-6265b614c8b2` (from analysis of a different job earlier)
- ❌ An old text from history

**RIGHT:** Using the CURRENT, MOST RECENT job UUID
- ✅ `b954620b-a2c7-4474-9249-f31d8d55856f` (from the latest "Analysis Ready" message in THIS conversation)

**Example - What to look for:**
```
Most recent message in conversation:
———————————————
📊 Analysis Ready: sample_name
Run UUID: b954620b-a2c7-4474-9249-f31d8d55856f     ← THIS IS WHAT YOU USE

Use this UUID in EVERY tool call:
[[DATA_CALL: service=server4, tool=find_file, run_uuid=b954620b-a2c7-4474-9249-f31d8d55856f, file_name=...]]
[[DATA_CALL: service=server4, tool=parse_..., run_uuid=b954620b-a2c7-4474-9249-f31d8d55856f, file_path=...]]
———————————————
```

**Rule:** If you see multiple "Analysis Ready" messages in conversation history, use ONLY the last one. The most recent one is the active job.

---

## Directory Prefix Requirement - Unmissable Warning

### 🚨 UNMISSABLE WARNING 🚨

**⚡ THE #1 ERROR THAT CAUSES "FILE NOT FOUND" ⚡**

**❌ WRONG:** `file_path=filename.ext` (no directory)
- This WILL fail with error: "File not found"
- System looks in: `/work_dir/filename.ext` (NOT THERE)

**✅ RIGHT:** `file_path=directory/filename.ext` (with directory)
- This will WORK
- System looks in: `/work_dir/directory/filename.ext` (FOUND)

**THE RULE:** Directory prefix (`annot/`, `bedMethyl/`, `counts/`, `modkit/`) is ALWAYS REQUIRED

---

## Step-by-Step Workflow: User Asks to Parse a File

When user says "parse [filename]":

# ⚠️⚠️⚠️ EXECUTE IMMEDIATELY - DO NOT DESCRIBE ⚠️⚠️⚠️
# Do NOT print messages like "I will parse the file"
# Do NOT explain the steps
# Just execute the DataCalls below right now
# ⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️⚠️

### STEP 1: EXECUTE NOW - Call find_file

Replace `{filename}` with the actual file being searched:
```
[[DATA_CALL: service=server4, tool=find_file, run_uuid={uuid}, file_name={filename}]]
```

### STEP 2: Extract `primary_path` from the response

The response will look like this:
```json
{
  "success": true,
  "primary_path": "directory/filename.ext",  ← COPY THIS ENTIRE STRING
  "paths": ["directory/filename.ext"],       ← Only result paths (work/ excluded)
  "file_count": 1
}
```

### STEP 3: Copy the ENTIRE `primary_path` value EXACTLY

Including the directory prefix:
- ✅ DO: `directory/filename.ext` (full path with directory)
- ❌ DO NOT: `filename.ext` (missing directory prefix)
- ❌ DO NOT: Extract partial filenames
- ❌ DO NOT: Modify case

### STEP 4: Use it in parse call - COPY THE EXACT VALUE

Replace `{tool}` with `parse_csv_file`, `parse_bed_file`, or `read_file_content`:
```
[[DATA_CALL: service=server4, tool={tool}, run_uuid={uuid}, file_path=directory/filename.ext]]
```

### STEP 5: WHEN parse SUCCEEDS - Present the data immediately

✅ If the response has `"success": true`:
- You have received the data successfully
- Do NOT say "The query did not return the expected data"
- Do NOT ask for more data
- Present the data to the user in a clear format

---

## Demonstration: What Happens with Wrong vs Right Paths

**If you use:** `file_path=filename.ext`
- System looks for: `/media/backup_disk/agoutic_root/server3_work/{uuid}/filename.ext` ❌
- Result: **FILE NOT FOUND ERROR** (file isn't in root, it's in subdirectory)

**If you use:** `file_path=directory/filename.ext`
- System looks for: `/media/backup_disk/agoutic_root/server3_work/{uuid}/directory/filename.ext` ✅
- Result: **SUCCESS** (file found and parsed)

**Key:** The directory prefix (subdirectory) IS REQUIRED and CANNOT be omitted.

---

## Critical Rules - Apply to All Modes

**Before calling any parse tool, verify:**
- ✅ UUID is from MOST RECENT "Analysis Ready" message
- ✅ `primary_path` includes a directory prefix
- ✅ Using EXACT copy of `primary_path` from find_file response
- ✅ Not modifying or shortening the path
- ✅ Not changing case
- ✅ Not ignoring the directory prefix

**DO:**
- Execute tool calls immediately — don't explain what you're about to do
- Present results with clear explanations of what they mean
- Ask clarifying questions if user's intent is ambiguous
- Offer suggestions for further analysis based on what you find

**DON'T:**
- Explain your process step-by-step before executing
- Ask permission for obvious next steps
- Get stuck in explanation loops — act first, explain results
- Say "the query did not return expected data" when `success: true`

---

## Reference: Mode-Specific Tools and Directories

Each Dogme skill documents the specific files and tools for its mode:

- **Dogme_cDNA**: Uses `parse_csv_file` for gene counts, `bedMethyl/` and `annot/` directories
- **Dogme_DNA**: Uses `parse_bed_file` for modification calls, `bedMethyl/` directories
- **Dogme_RNA**: Uses `parse_csv_file` and `read_file_content`, `modkit/` and `counts/` directories

See the mode-specific skill file for which files to search for and how to interpret the results.
