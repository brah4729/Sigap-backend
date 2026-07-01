"""
config.py — Central configuration helper for SIGAP.

Why have this file?
  Without it, every agent file would duplicate the same logic.
  With this file, every agent just calls:
    from config import use_api_key_for, GEMINI_MODEL
    use_api_key_for("monitor")

How API key switching works in ADK 2.x:
  The old google-generativeai had genai.configure(api_key=...).
  The new google-genai (used by ADK 2.x) removed that method.
  ADK now reads GOOGLE_API_KEY from the environment automatically.
  So we just set os.environ["GOOGLE_API_KEY"] before running each agent.

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
      3. Raises ValueError with a helpful message
    """
    env_var = _KEY_MAP.get(agent_name.lower())

    # Try agent-specific key first
    if env_var:
        key = os.getenv(env_var, "").strip()
        if key and "your_" not in key:
            return key

    # Fall back to default key
    default_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if default_key and "your_" not in default_key:
        return default_key

    raise ValueError(
        f"No API key found for agent '{agent_name}'.\n"
        f"Set {env_var or 'GOOGLE_API_KEY'} in your .env file.\n"
        f"Get a free key at: https://aistudio.google.com/apikey"
    )


def use_api_key_for(agent_name: str) -> None:
    """
    Set GOOGLE_API_KEY in the environment for the given agent.

    Why os.environ instead of genai.configure()?
      ADK 2.x removed genai.configure(). The new google-genai package
      reads GOOGLE_API_KEY from the environment automatically.
      Setting os.environ["GOOGLE_API_KEY"] is the correct approach.

    Note: This works fine for our use case because FastAPI processes
    one agent request at a time (we don't run agents concurrently).
    In a high-concurrency system you'd use separate processes instead.
    """
    key = get_api_key(agent_name)
    os.environ["GOOGLE_API_KEY"] = key
    print(f"[Config] Using API key for '{agent_name}' "
          f"(ends with ...{key[-6:]})")


def get_all_key_status() -> dict:
    """
    Returns which keys are configured vs missing.
    Safe to expose — shows key status but never the actual key values.
    """
    status = {}
    default_key = os.getenv("GOOGLE_API_KEY", "")
    has_default = bool(default_key and "your_" not in default_key)

    for agent, env_var in _KEY_MAP.items():
        specific_key = os.getenv(env_var, "")
        has_specific = bool(specific_key and "your_" not in specific_key)

        status[agent] = {
            "env_var": env_var,
            "has_specific_key": has_specific,
            "has_fallback": has_default,
            "will_work": has_specific or has_default,
        }

    return status
