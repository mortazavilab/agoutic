import os
from openai import OpenAI
# Update import to use LLM_URL
try:
    from server1.config import SKILLS_DIR, SKILLS_REGISTRY, LLM_URL, LLM_MODELS
except ImportError:
    from config import SKILLS_DIR, SKILLS_REGISTRY, LLM_URL, LLM_MODELS
    
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
        """
        skill_content = self._load_skill_text(skill_key)
        
        system_prompt = f"""
        You are Agoutic, an autonomous bioinformatics agent.
        
        YOUR CURRENT SKILL: {skill_key}
        
        INSTRUCTIONS:
        The user will ask for a task. You must strictly follow the "Plan Logic" 
        defined in the skill below.
        
        --- SKILL DEFINITION START ---
        {skill_content}
        --- SKILL DEFINITION END ---
        
        OUTPUT FORMAT:
        You must reply with a structured plan. 
        Simply list the steps you intend to take in clear natural language, 
        prefixed with "STEP [N]:".
        """
        return system_prompt

    def think(self, user_message: str, skill_key: str = "ENCODE_LongRead"):
        """
        Sends the skill + user request to the local LLM and gets the plan.
        """
        print(f"🧠 Loading Skill: {skill_key}")
        print(f"🔌 Connecting to LLM at {LLM_URL} using model: {self.display_name}...")
        
        system_prompt = self.construct_system_prompt(skill_key)
        
        try:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.1  # Low temp = more obedient to instructions
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            return f"❌ Brain Freeze (Connection Error): {str(e)}"

# --- Quick Test Block ---
# In server1/agent_engine.py (bottom test block)
if __name__ == "__main__":
    # Test the fast model specifically
    engine = AgentEngine(model_key="fast") 
    
    print(f"Using model: {engine.model_name}")
    print(engine.think("Hello, are you ready?"))