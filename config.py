"""
config.py — Central configuration helper for SIGAP.

Why have this file?
  Without it, every agent file would duplicate the same logic:
    key = os.getenv("GOOGLE_API_KEY_MONITOR") or os.getenv("GOOGLE_API_KEY")
  
  With this file, every agent just calls:
    from config import get_api_key
    key = get_api_key("monitor")

  Single source of truth — if we change the key naming convention,
  we only update it here, not in 4 different agent files.

Key fallback chain:
  1. Agent-specific key (e.g. GOOGLE_API_KEY_MONITOR)
  2. Default key (GOOGLE_API_KEY)
  3. Raises clear error so you know exactly what's missing
"""

import os
from dotenv import load_dotenv

load_dotenv()

# The model all agents use — change here to update everywhere
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

# Map agent names to their environment variable names
_KEY_MAP = {
    "monitor":      "GOOGLE_API_KEY_MONITOR",
    "assessment":   "GOOGLE_API_KEY_ASSESSMENT",
    "coordinator":  "GOOGLE_API_KEY_COORDINATOR",
    "orchestrator": "GOOGLE_API_KEY_ORCHESTRATOR",
}


def get_api_key(agent_name: str) -> str:
    """
    Get the API key for a specific agent.

    Fallback chain:
      1. Agent-specific env var  (e.g. GOOGLE_API_KEY_MONITOR)
      2. Default env var         (GOOGLE_API_KEY)
      3. Raises ValueError       (clear error message)

    Example:
      get_api_key("monitor")
      → reads GOOGLE_API_KEY_MONITOR first
      → falls back to GOOGLE_API_KEY if not set
    """
    env_var = _KEY_MAP.get(agent_name.lower())
    
    # Try agent-specific key first
    if env_var:
        key = os.getenv(env_var)
        if key and key != f"your_{agent_name}_key_here":
            return key

    # Fall back to default key
    default_key = os.getenv("GOOGLE_API_KEY")
    if default_key and default_key != "your_default_key_here":
        return default_key

    # Nothing found — give a helpful error
    raise ValueError(
        f"No API key found for agent '{agent_name}'.\n"
        f"Set {env_var or 'GOOGLE_API_KEY'} in your .env file.\n"
        f"Get a free key at: https://aistudio.google.com/apikey"
    )


def get_all_key_status() -> dict:
    """
    Returns which keys are configured vs missing.
    Used by the /api/config/keys endpoint so you can
    check key status without exposing actual key values.
    """
    status = {}
    default_key = os.getenv("GOOGLE_API_KEY", "")
    has_default = bool(default_key and default_key != "your_default_key_here")

    for agent, env_var in _KEY_MAP.items():
        specific_key = os.getenv(env_var, "")
        has_specific = bool(specific_key and specific_key != f"your_{agent}_key_here")
        
        status[agent] = {
            "env_var": env_var,
            "has_specific_key": has_specific,
            "has_fallback": has_default,
            "will_work": has_specific or has_default,
        }

    return status
