import os
from openai import OpenAI
from server1.config import SKILLS_DIR, SKILLS_REGISTRY, LLM_URL, LLM_MODELS
from server1.config import get_source_for_skill, SERVICE_REGISTRY
from server2.config import CONSORTIUM_REGISTRY

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
        
        # Build list of all available skills
        all_skills = "\n".join([f"  - {key}" for key in SKILLS_REGISTRY.keys()])
        
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
        
        system_prompt = f"""
        You are Agoutic, an autonomous bioinformatics agent.
        
        AVAILABLE SKILLS:
{all_skills}
        
        YOUR CURRENT SKILL: {skill_key}
        {data_call_block}
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

    def think(self, user_message: str, skill_key: str = "ENCODE_Search", conversation_history: list = None):
        """
        Sends the skill + user request to the local LLM and gets the plan.
        
        Args:
            user_message: The current user message
            skill_key: The skill to use
            conversation_history: List of previous messages in format [{"role": "user/assistant", "content": "..."}]
        """
        print(f"🧠 Loading Skill: {skill_key}")
        print(f"🔌 Connecting to LLM at {LLM_URL} using model: {self.display_name}...")
        
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