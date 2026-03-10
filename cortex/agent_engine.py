import os
import re
from pathlib import Path
from openai import OpenAI
from cortex.config import SKILLS_DIR, SKILLS_REGISTRY, LLM_URL, LLM_MODELS, LLM_NUM_CTX
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
logger.info("LLM connection configured", llm_url=LLM_URL, num_ctx=LLM_NUM_CTX)

PROMPT_TEMPLATES_DIR = Path(__file__).resolve().parent / "prompt_templates"
FIRST_PASS_TEMPLATE = "first_pass_system_prompt.md"
SECOND_PASS_TEMPLATE = "second_pass_system_prompt.md"


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

    def _read_template(self, template_name: str) -> str:
        template_path = PROMPT_TEMPLATES_DIR / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"Prompt template missing: {template_path}")
        return template_path.read_text(encoding="utf-8")

    def _render_template(self, template_name: str, **context) -> str:
        return self._read_template(template_name).format(**context)

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

    def _build_available_skills_text(self) -> str:
        """Build the human-readable list of available skills for the prompt."""
        analysis_skills = {"run_dogme_dna", "run_dogme_rna", "run_dogme_cdna"}
        skill_lines = []
        for key in SKILLS_REGISTRY.keys():
            if key in analysis_skills:
                skill_lines.append(
                    f"  - {key} (analysis interpretation — do NOT use for job submission)"
                )
            else:
                skill_lines.append(f"  - {key}")
        return "\n".join(skill_lines)

    def _build_data_call_block(self, skill_key: str) -> str:
        """Build dynamic DATA_CALL guidance and tool contracts for the active skill."""
        source_info = get_source_for_skill(skill_key)
        if not source_info:
            return ""

        source_key, source_type = source_info
        if source_type == "consortium":
            tag_prefix = f"consortium={source_key}"
            registry = CONSORTIUM_REGISTRY[source_key]
        else:
            tag_prefix = f"service={source_key}"
            registry = SERVICE_REGISTRY[source_key]

        display_name = registry.get("display_name", source_key.upper())

        if source_key == "encode":
            examples = """
        ✅ CORRECT EXAMPLES:
        [[DATA_CALL: consortium=encode, tool=get_experiment, accession=ENCSR123ABC]]
        [[DATA_CALL: consortium=encode, tool=search_by_biosample, search_term=K562, organism=Homo sapiens]]
        [[DATA_CALL: consortium=encode, tool=get_files_by_type, accession=ENCSR123ABC]]

        ❌ FORBIDDEN - NEVER WRITE THESE:
        Get Experiment (accession=ENCSR123ABC)        ❌ NO BRACKETS - WILL NOT EXECUTE
        **Get Experiment** (accession=ENCSR123ABC)    ❌ NO BRACKETS - WILL NOT EXECUTE
        Get Files By Type (accession=ENCSR123ABC)     ❌ NO BRACKETS - WILL NOT EXECUTE"""
        elif source_key == "launchpad":
            examples = """
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
            examples = """
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

        tool_contract = format_tool_contract(source_key, source_type)
        if tool_contract:
            data_call_block += f"""
═══════════════════════════════════════════════════════════════════════════════
📋 TOOL PARAMETER CONTRACTS — Authoritative reference for all tools below.
Use ONLY the parameter names listed here. Do NOT invent parameter names.
═══════════════════════════════════════════════════════════════════════════════

{tool_contract}
═══════════════════════════════════════════════════════════════════════════════
"""

        return data_call_block

    def construct_analysis_prompt(self) -> str:
        """Render the second-pass analysis prompt from its template."""
        return self._render_template(SECOND_PASS_TEMPLATE)

    def render_system_prompt(self, skill_key: str = "welcome", prompt_type: str = "first_pass") -> str:
        """Render the current system prompt for inspection or execution."""
        if prompt_type == "first_pass":
            return self.construct_system_prompt(skill_key)
        if prompt_type == "second_pass":
            return self.construct_analysis_prompt()
        raise ValueError(f"Unknown prompt_type: {prompt_type}")

    def construct_system_prompt(self, skill_key: str) -> str:
        """
        Combines the 'Persona' with the specific 'Skill' instructions.
        Dynamically generates DATA_CALL tag examples based on which
        consortium or service the active skill belongs to.
        """
        skill_content = self._load_skill_text(skill_key)
        return self._render_template(
            FIRST_PASS_TEMPLATE,
            skill_content=skill_content,
            all_skills=self._build_available_skills_text(),
            data_call_block=self._build_data_call_block(skill_key),
            skill_key=skill_key,
        )

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
                temperature=0.1,  # Low temp = more obedient to instructions
                extra_body={"options": {"num_ctx": LLM_NUM_CTX}},
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
        logger.info(
            "Analyzing tool results",
            skill=skill_key,
            model=self.display_name,
            data_size=len(data_results),
        )

        system_prompt = self.construct_analysis_prompt()

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
                extra_body={"options": {"num_ctx": LLM_NUM_CTX}},
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