# Skill Routing Pattern

## Problem

When an agent is in a specific skill context and the user asks an unrelated question, the agent needs clear guidance about:
1. What topics are IN SCOPE for the current skill
2. What topics are OUT OF SCOPE  
3. When and where to route to other skills
4. How to handle ambiguous or off-topic questions

## Solution: Standard "Skill Scope & Routing" Section

Every skill should have a standardized section after the Description that clearly defines scope boundaries and routing rules.

## Template

```markdown
## Skill Scope & Routing

### ✅ This Skill Handles:
- [List specific question types and tasks this skill covers]
- [Include example questions]

### ❌ This Skill Does NOT Handle:
- **Topic Category** → `[[SKILL_SWITCH_TO: target_skill]]`
  - Example: "Question text"
  - Example: "Another question"
  
- **Another Topic** → `[[SKILL_SWITCH_TO: other_skill]]`
  - Example: "Question text"

### 🔀 General Routing Rules:

**If the user asks about:**
- **New local data / file paths / "analyze my data"** → `[[SKILL_SWITCH_TO: analyze_local_sample]]`
- **ENCODE experiments / accessions / "search ENCODE"** → `[[SKILL_SWITCH_TO: encode_search]]`
- **Results of completed jobs / "QC report" / "show me files"** → `[[SKILL_SWITCH_TO: analyze_job_results]]`
- **General help / "what can you do" / unclear intent** → `[[SKILL_SWITCH_TO: welcome]]`

**When uncertain:** If the user's question is clearly outside this skill's expertise, prefer switching to the appropriate skill over saying "I can't help with that."
```

## Application to Each Skill

### 1. Welcome Skill
- Scope: General routing, introducing capabilities
- Routes to: analyze_local_sample, encode_search, analyze_job_results
- Should NOT handle: Specific data operations

### 2. Local Sample Intake (analyze_local_sample)
- Scope: Gathering metadata for local data submission
- Routes to: 
  - analyze_job_results (if analyzing completed jobs)
  - encode_search (if user mentions ENCODE)
  - welcome (if user asks "what can you do")

### 3. ENCODE Search (encode_search)
- Scope: Searching and browsing ENCODE Portal
- Routes to:
  - ENCODE_LongRead (when user wants to download/process)
  - analyze_job_results (if analyzing completed Dogme jobs)
  - analyze_local_sample (if user switches to local data)

### 4. ENCODE Long-Read (download and process ENCODE data)
- Scope: Downloading ENCODE files and submitting to Dogme
- Routes to:
  - encode_search (if user wants to search more experiments)
  - analyze_job_results (when job completes)
  - analyze_local_sample (if user switches to local data)

### 5. Analyze Job Results (analyze_job_results)
- Scope: Initial job verification, routing to mode-specific analysis
- Routes to:
  - run_dogme_dna (for DNA/Fiber-seq results)
  - run_dogme_rna (for RNA results)
  - run_dogme_cdna (for cDNA results)
  - encode_search (if user asks about ENCODE accessions)
  - analyze_local_sample (if user wants to submit new job)

### 6. Dogme DNA/RNA/cDNA (run_dogme_*)
- Scope: Mode-specific result interpretation ONLY
- Routes to:
  - encode_search (if user asks about ENCODE data)
  - analyze_job_results (if user wants to analyze a different job)
  - analyze_local_sample (if user wants to submit new job)
  - welcome (if user asks general questions)

## Key Principles

1. **Be explicit**: Every skill should explicitly list what it cannot do
2. **Provide examples**: Include real question examples that trigger switches
3. **Default to routing**: When uncertain, switch to the appropriate skill rather than refusing
4. **Maintain context**: Brief handoff message before switching (e.g., "I'll switch to the ENCODE search skill to help with that")
5. **Avoid dead ends**: Every skill should have escape hatches to other relevant skills

## Implementation Pattern

When adding to a skill:
1. Place immediately after ## Description
2. Be specific about scope boundaries
3. Include 2-3 example questions for each routing rule
4. Add general fallback rules to common skills (welcome, analyze_local_sample, encode_search)

This ensures agents always know where to go when the user changes topics or asks something outside the current skill's domain.
