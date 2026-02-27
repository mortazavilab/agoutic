import os
import re
from openai import OpenAI
from cortex.config import SKILLS_DIR, SKILLS_REGISTRY, LLM_URL, LLM_MODELS
from cortex.config import get_source_for_skill, SERVICE_REGISTRY
from cortex.tool_contracts import format_tool_contract
from atlas.config import CONSORTIUM_REGISTRY
from common.logging_config import get_logger

logger = get_logger(__name__)

# --- LLM CONNECTION ---
# We use the standard OpenAI client but point it to the configured URL
client = OpenAI(
    base_url=LLM_URL,
    api_key="ollama",  # Required by the library, but ignored by Ollama
    timeout=240.0,  # Must finish before UI's 300s timeout
)


def _usage_to_dict(usage_obj) -> dict:
    """Convert an OpenAI UsageObject to a plain dict, handling None gracefully."""
    if usage_obj is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
    }

class AgentEngine:
    def __init__(self, model_key="default"):
        """
        Initialize the brain with a specific model preference.
        
        Args:
            model_key (str): The key from LLM_MODELS in config.py (e.g., 'default', 'fast')
                             OR a direct model name string (e.g., 'mistral').
        """
        # 1. Try to find the friendly name in our config map
        if model_key in LLM_MODELS:
            self.model_name = LLM_MODELS[model_key]
            self.display_name = f"{model_key} ({self.model_name})"
        else:
            # Fallback: If user passes a raw string not in config, use it directly
            self.model_name = model_key
            self.display_name = model_key

    def _load_skill_text(self, skill_key: str) -> str:
        """
        Reads the Markdown content of a specific skill from the skills/ folder.
        Also automatically includes any referenced .md files using markdown link patterns.
        
        Detects patterns like [filename.md](filename.md) and auto-loads those files.
        """
        if skill_key not in SKILLS_REGISTRY:
            raise ValueError(f"Skill '{skill_key}' not found in Registry.")
        
        filename = SKILLS_REGISTRY[skill_key]
        file_path = SKILLS_DIR / filename
        
        if not file_path.exists():
            raise FileNotFoundError(f"Skill file missing: {file_path}")
            
        with open(file_path, "r") as f:
            skill_content = f.read()
        
        # Auto-detect and load referenced .md files
        # Pattern: [filename.md](filename.md) — both must match
        pattern = r'\[([a-zA-Z0-9_\-\.]+\.md)\]\(\1\)'
        referenced_files = re.findall(pattern, skill_content)
        
        # Load each referenced file and append to content
        for ref_filename in set(referenced_files):  # set() to avoid duplicates
            ref_file_path = SKILLS_DIR / ref_filename
            if ref_file_path.exists():
                try:
                    with open(ref_file_path, "r") as f:
                        ref_content = f.read()
                    # Append with a clear section marker
                    skill_content += f"\n\n{'='*80}\n"
                    skill_content += f"[INCLUDED REFERENCE: {ref_filename}]\n"
                    skill_content += f"{'='*80}\n\n"
                    skill_content += ref_content
                except Exception as e:
                    logger.warning(f"Failed to load referenced file {ref_filename}: {e}")
            else:
                logger.warning(f"Referenced file not found: {ref_filename} (referenced from {filename})")
        
        return skill_content

    def construct_system_prompt(self, skill_key: str) -> str:
        """
        Combines the 'Persona' with the specific 'Skill' instructions.
        Dynamically generates DATA_CALL tag examples based on which
        consortium or service the active skill belongs to.
        """
        skill_content = self._load_skill_text(skill_key)
        
        # Build list of all available skills with brief descriptions
        # Dogme skills are analysis interpretation guides — show them with
        # a note so the LLM knows they're for post-job analysis, not submission.
        analysis_skills = {"run_dogme_dna", "run_dogme_rna", "run_dogme_cdna"}
        skill_lines = []
        for key in SKILLS_REGISTRY.keys():
            if key in analysis_skills:
                skill_lines.append(f"  - {key} (analysis interpretation — do NOT use for job submission)")
            else:
                skill_lines.append(f"  - {key}")
        all_skills = "\n".join(skill_lines)
        
        # Determine the data source for this skill (if any)
        source_info = get_source_for_skill(skill_key)
        
        # Build the DATA_CALL tag instructions dynamically
        data_call_block = ""
        if source_info:
            source_key, source_type = source_info
            
            if source_type == "consortium":
                tag_prefix = f"consortium={source_key}"
                registry = CONSORTIUM_REGISTRY[source_key]
            else:
                tag_prefix = f"service={source_key}"
                registry = SERVICE_REGISTRY[source_key]
            
            display_name = registry.get("display_name", source_key.upper())
            
            # Build examples based on the source type
            if source_key == "encode":
                examples = f"""
        ✅ CORRECT EXAMPLES:
        [[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
        [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens]]
        [[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR123ABC]]
        
        ❌ FORBIDDEN - NEVER WRITE THESE:
        Get Experiment (accession=ENCSR123ABC)        ❌ NO BRACKETS - WILL NOT EXECUTE
        **Get Experiment** (accession=ENCSR123ABC)    ❌ NO BRACKETS - WILL NOT EXECUTE
        Get Files By Type (accession=ENCSR123ABC)     ❌ NO BRACKETS - WILL NOT EXECUTE"""
            elif source_key == "launchpad":
                examples = f"""
        ✅ CORRECT EXAMPLES:
        [[DATA_CALL: service=launchpad, tool=submit_dogme_job, sample_name=liver_rep1, mode=DNA, reference_genome=GRCh38, input_directory=/data/samples/pod5/]]
        [[DATA_CALL: service=launchpad, tool=submit_dogme_job, sample_name=sample1, mode=CDNA, reference_genome=mm39, input_directory=/data/fastq/, input_type=fastq]]
        [[DATA_CALL: service=launchpad, tool=check_nextflow_status, run_uuid=4d9376a5-5a4b-4642-86cd-78f7a63fab3d]]
        
        NOTE: input_directory can contain pod5, bam, or fastq files (set input_type accordingly).
        NOTE: reference_genome can be a single genome or a comma-separated list for parallel multi-genome analysis.
        NOTE: For the analyze_local_sample skill, do NOT use DATA_CALL tags.
        Instead, collect all parameters and output [[APPROVAL_NEEDED]].
        The system will automatically submit the job after user approval.
        
        ❌ FORBIDDEN - NEVER WRITE THESE:
        Submit Dogme Job (sample_name=...)             ❌ NO BRACKETS - WILL NOT EXECUTE
        run_dogme_rna(...)                             ❌ NOT A REAL TOOL - WILL FAIL
        run_dogme_cdna(...)                            ❌ NOT A REAL TOOL - WILL FAIL"""
            elif source_key == "analyzer":
                examples = f"""
        ✅ CORRECT EXAMPLES:
        [[DATA_CALL: service=analyzer, tool=get_analysis_summary, work_dir=/path/to/workflow]]
        [[DATA_CALL: service=analyzer, tool=list_job_files, work_dir=/path/to/workflow, extensions=.csv,.tsv,.bed,.txt]]
        [[DATA_CALL: service=analyzer, tool=parse_csv_file, work_dir=/path/to/workflow, file_name=final_stats.csv]]
        [[DATA_CALL: service=analyzer, tool=find_file, work_dir=/path/to/workflow, file_name=final_stats]]
        
        ❌ FORBIDDEN - NEVER WRITE THESE:
        Get Analysis Summary (run_uuid=...)           ❌ NO BRACKETS - WILL NOT EXECUTE
        [[TOOL_CALL: GET /analysis/jobs/...]]         ❌ WRONG TAG NAME - WILL NOT EXECUTE
        STEP 1: Get the summary...                    ❌ NARRATION - JUST EMIT THE TAG"""
            else:
                examples = f"""
        ✅ CORRECT EXAMPLE:
        [[DATA_CALL: {tag_prefix}, tool=tool_name, param1=value1, param2=value2]]
        
        ❌ FORBIDDEN - NEVER WRITE THESE:
        Tool Name (param1=value1)                     ❌ NO BRACKETS - WILL NOT EXECUTE"""
            
            data_call_block = f"""
        ═══════════════════════════════════════════════════════════════════════════════
        🚨 CRITICAL: DATA CALL TAG FORMAT - READ THIS BEFORE DOING ANYTHING 🚨
        ═══════════════════════════════════════════════════════════════════════════════
        
        When querying {display_name}, you MUST use this EXACT tag format:
        
        [[DATA_CALL: {tag_prefix}, tool=tool_name, param1=value1, param2=value2]]
        {examples}
        
        If you write plain text without [[double brackets]], the tool will NOT run and you 
        will be forced to hallucinate results from memory instead of retrieving real data!
        
        ═══════════════════════════════════════════════════════════════════════════════
        """
        
        # --- Tool Schema Contract: auto-generated from MCP server schemas ---
        _tool_contract = ""
        if source_info:
            _tool_contract = format_tool_contract(source_key, source_type)
        if _tool_contract:
            data_call_block += f"""
═══════════════════════════════════════════════════════════════════════════════
📋 TOOL PARAMETER CONTRACTS — Authoritative reference for all tools below.
Use ONLY the parameter names listed here. Do NOT invent parameter names.
═══════════════════════════════════════════════════════════════════════════════

{_tool_contract}
═══════════════════════════════════════════════════════════════════════════════
"""
        
        system_prompt = f"""You are Agoutic, an autonomous bioinformatics agent.
You specialize in processing and analyzing long-read data, such as long-read RNA-seq (also called long read RNA-seq or LR-RNA-seq)and genomic DNA.
The long-read RNA may be either cDNA or direct RNA. 
The genomeic DNA may be either native, which has DNA methylation information, 
or Fiber-seq, which has in addition unique chromatin modification information embedded in the reads.

You are proficient in using the Dogme pipeline for base calling, alignment, modification detection, and comprehensive analysis of long-read datasets.
You have expertise in handling various file types including FASTQ, BAM, and the native POD5 format using Dogme. 

Users will ask you to perform tasks that will involve either processing local data or querying external data sources. 
You have access to a growing library of "Skills" that define how to handle different tasks. 
Each skill belongs to a specific "Service" or "Consortium" that provides data or tools.

You have DIRECT ACCESS to the following data sources via [[DATA_CALL:...]] tags:
- **ENCODE Portal** (consortium=encode) — search experiments, get files, metadata
- **Local Sample Intake** — interview users to collect sample metadata (path, name, type, genome) and submit to Dogme pipelines
- **Local Execution Engine** (service=launchpad) — submit and monitor pipeline jobs
- **Local Analysis Engine** (service=analyzer) — analyze completed job results, browse project files

═══════════════════════════════════════════════════════════════════════════════
📂 FILE BROWSING — Available on ALL skills
═══════════════════════════════════════════════════════════════════════════════

You can browse the user's project files at any time, regardless of the active skill.
These commands route to the Analysis Engine (analyzer) automatically — you do NOT
need to be on a Dogme analysis skill.

AVAILABLE COMMANDS (use [[DATA_CALL:...]] tags):
  [[DATA_CALL: service=analyzer, tool=list_job_files, work_dir=<path>, max_depth=1]]

WHEN TO USE:
- "list workflows" → lists workflow directories in the project
- "list files" → lists files in the current workflow directory
- "list files in annot" → lists files in a specific subfolder
- "list files in workflow1/annot" → lists files in a specific workflow's subfolder

The system automatically resolves the correct project/workflow directory from context.
You do NOT need to guess the work_dir path — the system will override it.
Just emit the tag and the system handles the rest.
═══════════════════════════════════════════════════════════════════════════════

For any query about ENCODE data, you MUST use [[DATA_CALL:...]] tags.
The tags execute automatically and return real data. Do NOT tell the user
to check a website or suggest you lack access — use the tags instead.
{data_call_block}

═══════════════════════════════════════════════════════════════════════════════
� PLOTTING: Interactive Charts from DataFrames — USE TAGS, NOT CODE 🚨
═══════════════════════════════════════════════════════════════════════════════

When the user asks for a plot, chart, or visualization, you MUST output a
[[PLOT:...]] tag. The system renders the chart automatically. You do NOT
need to write any code.

❌ NEVER write Python code (matplotlib, plotly, seaborn, etc.) for plotting.
❌ NEVER write ```python code blocks for charts.
❌ NEVER say "here is code to create a plot".
✅ ALWAYS use the [[PLOT:...]] tag below — it renders an interactive chart automatically.

TAG FORMAT:
[[PLOT: type=<chart_type>, df=DF<N>, x=<column>, y=<column>, color=<column>, title=<title>, agg=<aggregation>]]

SUPPORTED CHART TYPES:
- histogram  — Distribution of a single numeric column. Requires: x. Optional: color, title.
- scatter    — Two numeric columns plotted against each other. Requires: x, y. Optional: color, title.
- bar        — Categorical counts or grouped aggregation. Requires: x. Optional: y, color, agg (count|sum|mean), title.
- box        — Distribution comparison across categories. Requires: x (category), y (numeric). Optional: color, title.
- heatmap    — Correlation matrix of all numeric columns. Requires: df. Optional: title.
- pie        — Proportion of categorical values. Requires: x (category). Optional: y (values), title.

PARAMETER RULES:
- df: MUST be a valid DF reference (e.g., DF1, DF5) from the conversation.
  When the user says "this", "it", "the data", or "the results" without
  specifying a DF number, use the MOST RECENT dataframe — check
  latest_dataframe in the [STATE] JSON.
- x / y: MUST be actual column names from that DataFrame
- color: Optional categorical column to group/color traces by
- agg: For bar charts — "count" (count rows per x category), "sum", or "mean"
- title: Optional chart title (short, descriptive)

MULTI-TRACE: Emit multiple [[PLOT:...]] tags with the same df= and type= to overlay traces.

✅ CORRECT — just write ONE line:
[[PLOT: type=pie, df=DF1, x=assay, title=Assay Distribution]]

❌ WRONG — NEVER write Python code like this:
```python
import matplotlib.pyplot as plt
plt.pie(...)  # ❌ DO NOT DO THIS
```

MORE EXAMPLES:
[[PLOT: type=histogram, df=DF1, x=Score, title=Score Distribution]]
[[PLOT: type=scatter, df=DF2, x=enrichment, y=pvalue, color=Biosample, title=Enrichment vs P-value]]
[[PLOT: type=bar, df=DF1, x=Assay, agg=count, title=Experiments by Assay Type]]
[[PLOT: type=box, df=DF3, x=Status, y=File Size, title=File Size by Status]]
[[PLOT: type=heatmap, df=DF2, title=Correlation Matrix]]
[[PLOT: type=pie, df=DF1, x=Assay, title=Assay Distribution]]

WHEN TO SUGGEST PLOTS:
- User explicitly asks: "plot", "chart", "visualize", "graph", "histogram", "scatter", "pie"
- After presenting search results with many rows, you MAY suggest a useful chart
- When analyzing QC metrics, suggest distribution plots for numeric columns
- Do NOT plot if the DataFrame has fewer than 3 rows

═══════════════════════════════════════════════════════════════════════════════

AVAILABLE SKILLS:
{all_skills}

YOUR CURRENT SKILL: {skill_key}

═══════════════════════════════════════════════════════════════════════════════
🛡️ TOOL FAILURE RULES — Follow these EXACTLY when a tool returns an error
═══════════════════════════════════════════════════════════════════════════════

- Tool returns error → Report the EXACT error to the user. NEVER guess or hallucinate the result.
- Accession not found → Ask the user to confirm the accession. Do NOT substitute a different one.
- work_dir missing / invalid path → Ask the user before proceeding. NEVER invent a file path.
- Empty result set → Say "no results found for [query]". Do NOT fabricate alternative results.
- Connection failed → Say the service is temporarily unavailable and suggest retrying.
- Permission denied → Tell the user they don't have access to that resource.
- Ambiguous request → Propose 2-3 specific options and let the user choose.

═══════════════════════════════════════════════════════════════════════════════

INSTRUCTIONS:
The user will ask for a task. You must strictly follow the "Plan Logic" 
defined in the skill below.

--- SKILL DEFINITION START ---
{skill_content}
--- SKILL DEFINITION END ---

OUTPUT FORMATTING RULES:
1. Write your plan in clear natural language (Markdown).
2. Use "STEP [N]:" for each action.
3. For plots/charts/visualizations, ONLY use [[PLOT:...]] tags. NEVER write Python code for plotting.
4. If you determine that a different skill would be more appropriate for this task,
   output this tag on a new line:
   
   [[SKILL_SWITCH_TO: skill_name]]
   
   Replace 'skill_name' with one of the available skills listed above.
4. CRITICAL: If the skill definition mentions an "APPROVAL GATE" or requires user confirmation 
   before proceeding (e.g. for downloading or computing), you MUST end your response 
   with this exact tag on a new line:
   
   [[APPROVAL_NEEDED]]
   
   Do not output this tag if you are just answering a question.
"""
        return system_prompt

    def think(self, user_message: str, skill_key: str = "welcome", conversation_history: list = None):
        """
        Sends the skill + user request to the local LLM and gets the plan.
        
        Args:
            user_message: The current user message
            skill_key: The skill to use
            conversation_history: List of previous messages in format [{"role": "user/assistant", "content": "..."}]
        """
        logger.info("Loading skill", skill=skill_key, model=self.display_name, llm_url=LLM_URL)
        
        system_prompt = self.construct_system_prompt(skill_key)
        
        # Build messages array with conversation history
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history if provided
        if conversation_history:
            messages.extend(conversation_history)
        
        # Add current message
        messages.append({"role": "user", "content": user_message})
        
        try:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.1  # Low temp = more obedient to instructions
            )

            return response.choices[0].message.content, _usage_to_dict(response.usage)

        except Exception as e:
            return f"❌ Brain Freeze (Connection Error): {str(e)}", _usage_to_dict(None)

    def analyze_results(self, user_message: str, first_pass_text: str,
                        data_results: str, skill_key: str = "welcome",
                        conversation_history: list = None):
        """
        Second-pass LLM call: given the raw tool results, produce a clean,
        user-facing analysis that directly answers the original question.

        Args:
            user_message: The original user question
            first_pass_text: The LLM's first-pass response (with tags stripped)
            data_results: Formatted tool results (compact markdown/tables)
            skill_key: The active skill (used for context)
            conversation_history: Previous conversation messages
        """
        logger.info("Analyzing tool results", skill=skill_key,
                     model=self.display_name, data_size=len(data_results))

        system_prompt = f"""You are Agoutic, an autonomous bioinformatics agent.

You previously executed data queries on behalf of the user.

IMPORTANT CONTEXT: The full raw data table is ALREADY displayed as an interactive 
dataframe directly below your response in the UI. The user can sort, filter, and 
browse all rows there. You do NOT need to reproduce rows from the raw data.

Your job is to write a SHORT, DIRECT answer with these rules:

## When to write prose only (no table from you):
- "How many X?" → one sentence stating the exact total from "Found N result(s)"
- "What is X?" → a brief text answer

## When to produce a summary aggregation table:
ONLY create a markdown table in your response if you are computing something NEW
from the data that is not already a plain list — for example:
- Counts grouped by a field (e.g. number of experiments per assay type)
- Counts grouped by output type, status, organism, etc.
- A filtered subset the user asked for (specific assay, target, etc.)

In these cases, produce ONLY the aggregation/summary table — NOT the raw rows.
The raw rows are in the interactive dataframe below.

## Rules:
1. **For count questions**: state the exact total from "Found N result(s)" first.
   NEVER reduce or alter this number.
2. **No row reproduction**: do NOT list individual experiment/file rows unless 
   the user asked for a specific item by accession. The dataframe already shows them.
3. **Aggregation tables are fine**: counts by assay type, output type, etc. — 
   these are useful summaries not present in the raw list.
4. **Be concise**: your entire response should be under 200 words.
5. If the data is empty or does not answer the question, say:
   "The query did not return the expected data." and describe what was returned.

🚨 NEVER invent or hallucinate data. Every number MUST come from the data below.
🚨 Do NOT output [[DATA_CALL:...]], [[SKILL_SWITCH_TO:...]], or [[APPROVAL_NEEDED]] tags.
"""

        messages = [{"role": "system", "content": system_prompt}]

        # Include conversation history for context
        if conversation_history:
            messages.extend(conversation_history)

        # The user's original question
        messages.append({"role": "user", "content": user_message})

        # The assistant's first-pass reasoning + the raw data
        messages.append({
            "role": "assistant",
            "content": first_pass_text,
        })

        # Inject the data as a follow-up system-like user turn
        messages.append({
            "role": "user",
            "content": (
                "The data queries have been executed. Here are the results:\n\n"
                f"{data_results}\n\n"
                "Answer my original question concisely (under 200 words).\n"
                "The full data table is already shown as an interactive dataframe "
                "in the UI — do NOT reproduce individual rows.\n"
                "For count questions: state the exact total from 'Found N result(s)' first.\n"
                "Only produce a table if you are summarising/aggregating "
                "(e.g. counts per assay type) — use the 📊 Summary already in the data "
                "if it is present, rather than re-computing it."
            ),
        })

        try:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.1,
            )
            return response.choices[0].message.content, _usage_to_dict(response.usage)
        except Exception as e:
            logger.error("Second-pass analysis failed", error=str(e))
            # Fallback: return the formatted data directly
            return f"{first_pass_text}\n\n{data_results}", _usage_to_dict(None)

# --- Quick Test Block ---
if __name__ == "__main__":
    # 1. Initialize with the default model
    engine = AgentEngine(model_key="default")
    
    # 2. Define a test query
    test_query = "Please align the liver tissue sample using the latest reference."
    
    print("\n--- 🧑‍🔬 User Query ---")
    print(test_query)
    
    print(f"\n--- 🤖 Agent Thinking ---")
    reply, usage = engine.think(test_query)

    print("\n--- 📄 Result ---")
    print(reply)
    print("\n--- 📊 Token Usage ---")
    print(usage)