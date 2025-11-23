import requests
from typing import List

def fetch_llm_studio_models() -> List[str]:
    """
    Fetch available models from LLM Studio API.
    
    Returns:
        List[str]: List of available model IDs, or default fallback
    """
    try:
        response = requests.get("http://127.0.0.1:1234/v1/models", timeout=2)
        if response.status_code == 200:
            data = response.json()
            models = data.get('data', [])
            model_ids = [model.get('id', '') for model in models if model.get('id')]
            
            if model_ids:
                return model_ids
            else:
                return ["qwen/qwen3-4b-2507"]
        else:
            return ["qwen/qwen3-4b-2507"]
    except Exception:
        # Fallback if LLM Studio is not running or unreachable
        return ["qwen/qwen3-4b-2507"]
