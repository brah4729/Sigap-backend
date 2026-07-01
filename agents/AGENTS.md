# Agents Folder — AGENTS.md

Everything about the 4 AI agents lives here.
Read this before touching any agent file.

---

## What's In Here

```
agents/
├── monitor_agent.py      ← Scans BMKG/USGS for new disasters
├── assessment_agent.py   ← Deep analysis per disaster (population, risks, actions)
├── coordinator_agent.py  ← Deploys nearest resources to disasters
└── orchestrator_agent.py ← Big picture report using MCP tool data
```

---

## The Golden Rules for ADK 2.x Agents

These rules were learned the hard way. Breaking them causes hard-to-debug crashes.

### Rule 1: Always wrap messages in types.Content

```python
# ❌ ADK 1.x — broken in 2.x
runner.run_async(new_message="plain string")

# ✅ ADK 2.x — always do this
from google.genai import types
runner.run_async(new_message=types.Content(
    role="user",
    parts=[types.Part(text="your message here")]
))
```

### Rule 2: NEVER break out of run_async loop

```python
# ❌ NEVER — causes "Root node cancelled" + OpenTelemetry crashes
async for event in runner.run_async(...):
    if event.is_final_response():
        text = event.content.parts[0].text
        break  ← KILLS THE GENERATOR

# ✅ Let it finish naturally, overwrite on each final response
async for event in runner.run_async(...):
    if event.is_final_response() and event.content and event.content.parts:
        text = event.content.parts[0].text
# loop ends naturally — no break
```

### Rule 3: Always guard event.content

ADK fires many event types — tool calls, reasoning steps, etc.
Most have `content = None`. If you access `.parts` on None, you crash.

```python
# ❌ Crashes when content is None
text = event.content.parts[0].text

# ✅ Always triple-guard
if event.is_final_response() and event.content and event.content.parts:
    text = event.content.parts[0].text
```

### Rule 4: Always use _run_with_retry()

Gemini's free tier returns 503 (server busy) randomly.
Every agent has a `_run_with_retry()` helper that retries 3 times
with exponential backoff (2s → 4s → 8s). Always use it.

```python
# ❌ Will crash on 503
async for event in runner.run_async(...):
    ...

# ✅ Use retry wrapper
response = await _run_with_retry(agent, user_message)
```

### Rule 5: Always call use_api_key_for() at the start

```python
# At the top of every run_*_agent() function:
from config import use_api_key_for
use_api_key_for("monitor")   # or assessment / coordinator / orchestrator
```

This sets GOOGLE_API_KEY in the environment.
ADK reads that automatically — no other config needed.

---

## Agent Status Flow

```
MonitorAgent saves disaster as:      DETECTED
AssessmentAgent changes it to:       ASSESSING → RESPONDING
CoordinatorAgent changes it to:      RESOLVED
```

If a disaster is stuck in ASSESSING — the assessment agent failed midway.
Re-run assessment with: POST /api/agents/run/assessment?disaster_id=X

---

## Adding a New Agent — Checklist

- [ ] Create `agents/your_agent.py`
- [ ] Import `use_api_key_for` and call it first thing
- [ ] Use `_run_with_retry()` for all ADK calls
- [ ] Log every action with `_log_action(db, action, details, disaster_id)`
- [ ] Add key to `config.py` → `_KEY_MAP`
- [ ] Add env var to `.env` + `.env.example`
- [ ] Wire in `api/routes/agents.py`
