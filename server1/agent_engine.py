import os
from openai import OpenAI
from server1.config import SKILLS_DIR, SKILLS_REGISTRY, LLM_URL, LLM_MODELS
from server1.config import get_source_for_skill, SERVICE_REGISTRY
from server2.config import CONSORTIUM_REGISTRY
from common.logging_config import get_logger

logger = get_logger(__name__)

# --- LLM CONNECTION ---
# We use the standard OpenAI client but point it to the configured URL
client = OpenAI(
    base_url=LLM_URL,
    api_key="ollama",  # Required by the library, but ignored by Ollama
)

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
        """
        if skill_key not in SKILLS_REGISTRY:
            raise ValueError(f"Skill '{skill_key}' not found in Registry.")
        
        filename = SKILLS_REGISTRY[skill_key]
        file_path = SKILLS_DIR / filename
        
        if not file_path.exists():
            raise FileNotFoundError(f"Skill file missing: {file_path}")
            
        with open(file_path, "r") as f:
            return f.read()

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
            elif source_key == "server3":
                examples = f"""
        ✅ CORRECT EXAMPLES:
        [[DATA_CALL: service=server3, tool=submit_dogme_job, sample_name=liver_rep1, mode=DNA, reference_genome=GRCh38, input_directory=/data/samples/pod5/]]
        [[DATA_CALL: service=server3, tool=submit_dogme_job, sample_name=sample1, mode=CDNA, reference_genome=mm39, input_directory=/data/fastq/, input_type=fastq]]
        [[DATA_CALL: service=server3, tool=check_nextflow_status, run_uuid=4d9376a5-5a4b-4642-86cd-78f7a63fab3d]]
        
        NOTE: input_directory can contain pod5, bam, or fastq files (set input_type accordingly).
        NOTE: reference_genome can be a single genome or a comma-separated list for parallel multi-genome analysis.
        NOTE: For the analyze_local_sample skill, do NOT use DATA_CALL tags.
        Instead, collect all parameters and output [[APPROVAL_NEEDED]].
        The system will automatically submit the job after user approval.
        
        ❌ FORBIDDEN - NEVER WRITE THESE:
        Submit Dogme Job (sample_name=...)             ❌ NO BRACKETS - WILL NOT EXECUTE
        run_dogme_rna(...)                             ❌ NOT A REAL TOOL - WILL FAIL
        run_dogme_cdna(...)                            ❌ NOT A REAL TOOL - WILL FAIL"""
            elif source_key == "server4":
                examples = f"""
        ✅ CORRECT EXAMPLES:
        [[DATA_CALL: service=server4, tool=get_analysis_summary, run_uuid=4d9376a5-5a4b-4642-86cd-78f7a63fab3d]]
        [[DATA_CALL: service=server4, tool=categorize_job_files, run_uuid=4d9376a5-5a4b-4642-86cd-78f7a63fab3d]]
        [[DATA_CALL: service=server4, tool=list_job_files, run_uuid=4d9376a5-5a4b-4642-86cd-78f7a63fab3d]]
        
        ❌ FORBIDDEN - NEVER WRITE THESE:
        Get Analysis Summary (run_uuid=...)           ❌ NO BRACKETS - WILL NOT EXECUTE"""
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
- **Local Execution Engine** (service=server3) — submit and monitor pipeline jobs
- **Local Analysis Engine** (service=server4) — analyze completed job results

For any query about ENCODE data, you MUST use [[DATA_CALL:...]] tags.
The tags execute automatically and return real data. Do NOT tell the user
to check a website or suggest you lack access — use the tags instead.
{data_call_block}
AVAILABLE SKILLS:
{all_skills}

YOUR CURRENT SKILL: {skill_key}

INSTRUCTIONS:
The user will ask for a task. You must strictly follow the "Plan Logic" 
defined in the skill below.

--- SKILL DEFINITION START ---
{skill_content}
--- SKILL DEFINITION END ---

OUTPUT FORMATTING RULES:
1. Write your plan in clear natural language (Markdown).
2. Use "STEP [N]:" for each action.
3. If you determine that a different skill would be more appropriate for this task,
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
            
            return response.choices[0].message.content
            
        except Exception as e:
            return f"❌ Brain Freeze (Connection Error): {str(e)}"

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

You previously executed data queries on behalf of the user. The raw results 
are provided below. Your job is to:

1. **Answer the user's question directly** using ONLY the data provided below.
2. **Filter** the results to show only what is relevant to the question.
3. **Summarize** — present clean tables, counts, or bullet points.
4. **Do NOT dump raw JSON** — always present data in readable markdown tables.
5. If the data contains many file types but the user asked about a specific one 
   (e.g. BAM files), show ONLY that type.
6. Include relevant details like accession, output type, replicate, file size, 
   and status when showing file information.
7. Keep your response concise and well-structured.
8. If the data below is empty, contains errors, or does not answer the question,
   say exactly: "The query did not return the expected data." and describe what 
   was returned instead.

🚨 CRITICAL: NEVER invent, fabricate, or hallucinate data.
- Every accession number you mention MUST appear in the data below.
- Every file size, status, and output type MUST come from the data below.
- If you cannot find the answer in the data, say so. Do NOT make up accessions.
- Accession numbers follow the pattern ENCFF followed by exactly 6 alphanumeric characters.

IMPORTANT: Do NOT output any [[DATA_CALL:...]], [[SKILL_SWITCH_TO:...]], or 
[[APPROVAL_NEEDED]] tags. This is a final analysis pass — just present the answer.
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
                "Now analyze these results and provide a clear, filtered answer "
                "to my original question. Present the data in clean markdown tables. "
                "Only show what is relevant."
            ),
        })

        try:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.1,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("Second-pass analysis failed", error=str(e))
            # Fallback: return the formatted data directly
            return f"{first_pass_text}\n\n{data_results}"

# --- Quick Test Block ---
if __name__ == "__main__":
    # 1. Initialize with the default model
    engine = AgentEngine(model_key="default")
    
    # 2. Define a test query
    test_query = "Please align the liver tissue sample using the latest reference."
    
    print("\n--- 🧑‍🔬 User Query ---")
    print(test_query)
    
    print(f"\n--- 🤖 Agent Thinking ---")
    reply = engine.think(test_query)
    
    print("\n--- 📄 Result ---")
    print(reply)