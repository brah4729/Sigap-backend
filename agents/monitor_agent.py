"""
Monitor Agent — the "eyes" of SIGAP.

Responsibilities:
  - Periodically scan BMKG and GDACS for new disaster events
  - Deduplicate events (don't save the same quake twice)
  - Save new disasters to the database
  - Log every action for transparency

Google ADK 2.x notes:
  - run_async() requires types.Content, NOT a plain string
  - Never `break` out of run_async() — let it complete fully
    or the generator gets cancelled and crashes
  - event.content can be None for intermediate events (tool calls,
    internal steps) — always guard with `if event.content`
"""

import json

import asyncio

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.genai.errors import ServerError, ClientError

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import Disaster, AgentLog, DisasterType, SeverityLevel, DisasterStatus
from tools.bmkg_tool import fetch_latest_earthquakes, fetch_disaster_rss
from config import get_api_key, use_api_key_for, GEMINI_MODEL
from dotenv import load_dotenv
import os

load_dotenv()

MODEL = GEMINI_MODEL
AGENT_NAME = "MonitorAgent"


async def run_monitor_agent(db: AsyncSession) -> dict:
    """
    Runs the full Monitor Agent pipeline:
      1. Fetch raw data from BMKG + GDACS
      2. ADK Agent analyzes and filters significant events
      3. Save new disasters to DB with deduplication
    """
    print(f"[{AGENT_NAME}] Starting disaster scan...")

    # Configure Gemini to use this agent's specific API key.
    # This way each agent has its own quota — Monitor won't
    # exhaust Assessment's quota and vice versa.
    use_api_key_for("monitor")

    # Step 1: Fetch raw data
    earthquakes = await fetch_latest_earthquakes()
    rss_alerts = await fetch_disaster_rss()
    all_events = earthquakes + rss_alerts

    print(f"[{AGENT_NAME}] Found {len(all_events)} raw events from sources")

    if not all_events:
        await _log_action(db, "scan_complete", "No events found", None)
        await db.commit()
        return {"status": "ok", "new_disasters": 0, "message": "No new events found"}

    # Step 2: Build the agent
    agent = Agent(
        name=AGENT_NAME,
        model=MODEL,
        description="Disaster monitoring agent for Indonesia.",
        instruction="""
You are a disaster monitoring agent for Indonesia.

Analyze raw disaster event data from BMKG and GDACS.
Identify SIGNIFICANT events:
  - Earthquakes magnitude >= 5.0
  - Any flood, tsunami, volcano, or landslide

Return ONLY this JSON (no markdown, no explanation):
{
  "significant_events": [
    {
      "title": "string",
      "type": "EARTHQUAKE|FLOOD|TSUNAMI|VOLCANO|LANDSLIDE|OTHER",
      "severity": "LOW|MEDIUM|HIGH|CRITICAL",
      "location_name": "string",
      "latitude": 0.0,
      "longitude": 0.0,
      "source": "BMKG|GDACS",
      "description": "string",
      "needs_immediate_response": true
    }
  ]
}
""",
    )

    # Step 3: Prepare message — ADK 2.x requires types.Content, not plain string
    events_summary = json.dumps({
        "earthquakes": [
            {
                "title": e["title"],
                "magnitude": e.get("magnitude", 0),
                "location": e["location_name"],
                "latitude": e["latitude"],
                "longitude": e["longitude"],
                "potential": e.get("potential", ""),
                "source": e["source"],
            }
            for e in earthquakes
        ],
        "other_alerts": [
            {
                "title": a["title"],
                "type": a["type"],
                "description": a.get("description", "")[:200],
                "source": a["source"],
            }
            for a in rss_alerts
        ],
    })

    user_message = types.Content(
        role="user",
        parts=[types.Part(text=f"Analyze these disaster events:\n{events_summary}")],
    )

    # Step 4: Run agent — IMPORTANT rules for ADK 2.x async loop:
    #
    # Rule 1: Never use `break` — it cancels the generator mid-run and
    #         causes "Root node cancelled" + OpenTelemetry context errors.
    #
    # Rule 2: event.content can be None for intermediate steps (tool calls,
    #         internal reasoning steps). Always guard before accessing .parts
    #
    # Rule 3: Collect the LAST valid final response text, not the first.
    #         The agent may emit multiple events before finishing.

    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="sigap", user_id="system", session_id="monitor_scan"
    )

    runner = Runner(agent=agent, app_name="sigap", session_service=session_service)

    agent_response = ""  # will hold the last valid text from the agent

    # Retry loop — 503 means Google's servers are busy, not a code bug.
    # We wait and retry up to 3 times with exponential backoff.
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[{AGENT_NAME}] Calling Gemini (attempt {attempt}/{max_retries})...")

            # Let the generator run to FULL COMPLETION — no break!
            async for event in runner.run_async(
                user_id="system",
                session_id="monitor_scan",
                new_message=user_message,
            ):
                # Guard: event.content is None for non-text events
                if event.is_final_response() and event.content and event.content.parts:
                    agent_response = event.content.parts[0].text

            break  # success — exit retry loop

        except ServerError as e:
            # 503 = Google's servers are overloaded, temporary issue
            if attempt < max_retries:
                wait = 2 ** attempt  # exponential backoff: 2s, 4s, 8s
                print(f"[{AGENT_NAME}] 503 from Gemini, retrying in {wait}s... ({e})")
                await asyncio.sleep(wait)
                # Recreate session for next attempt
                session_service = InMemorySessionService()
                await session_service.create_session(
                    app_name="sigap", user_id="system", session_id="monitor_scan"
                )
                runner = Runner(agent=agent, app_name="sigap", session_service=session_service)
            else:
                print(f"[{AGENT_NAME}] All {max_retries} attempts failed with 503")
                await _log_action(db, "api_error", f"503 after {max_retries} retries: {str(e)[:200]}", None)
                await db.commit()
                return {
                    "status": "error",
                    "new_disasters": 0,
                    "message": "Gemini API temporarily unavailable (503). Try again in a few minutes.",
                }

        except ClientError as e:
            # 429 = quota exhausted — no point retrying
            print(f"[{AGENT_NAME}] Quota exceeded (429): {e}")
            await _log_action(db, "quota_error", str(e)[:200], None)
            await db.commit()
            return {
                "status": "error",
                "new_disasters": 0,
                "message": "API quota exhausted. Get a new key at https://aistudio.google.com/apikey",
            }

    print(f"[{AGENT_NAME}] Agent finished. Response length: {len(agent_response)} chars")

    # Step 5: Parse and save
    new_count = 0
    if not agent_response:
        print(f"[{AGENT_NAME}] Warning: empty agent response")
        await _log_action(db, "empty_response", "Agent returned no content", None)
        await db.commit()
        return {"status": "warning", "new_disasters": 0, "message": "Agent returned empty response"}

    try:
        # Strip markdown fences if present (``` json ... ```)
        clean = agent_response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            # Remove first line (```json) and last line (```)
            clean = "\n".join(lines[1:-1]).strip()

        parsed = json.loads(clean)
        significant_events = parsed.get("significant_events", [])
        print(f"[{AGENT_NAME}] Agent found {len(significant_events)} significant events")

        for event in significant_events:
            # Deduplication — skip if same title + location already exists
            existing = await db.execute(
                select(Disaster).where(
                    Disaster.title == event["title"],
                    Disaster.location_name == event.get("location_name"),
                )
            )
            if existing.scalar_one_or_none():
                print(f"[{AGENT_NAME}] Duplicate skipped: {event['title']}")
                continue

            # Save to DB
            disaster = Disaster(
                title=event["title"],
                description=event.get("description", ""),
                disaster_type=DisasterType(event.get("type", "OTHER")),
                severity=SeverityLevel(event.get("severity", "LOW")),
                status=DisasterStatus.DETECTED,
                location_name=event.get("location_name"),
                latitude=event.get("latitude"),
                longitude=event.get("longitude"),
                source=event.get("source", "BMKG"),
                ai_assessment=(
                    f"Detected by MonitorAgent. "
                    f"Needs immediate response: {event.get('needs_immediate_response', False)}"
                ),
            )
            db.add(disaster)
            await db.flush()  # get ID before commit

            await _log_action(db, "disaster_detected", json.dumps(event), disaster.id)
            new_count += 1
            print(f"[{AGENT_NAME}] Saved: {event['title']}")

        await db.commit()

    except json.JSONDecodeError as e:
        print(f"[{AGENT_NAME}] JSON parse error: {e}")
        print(f"[{AGENT_NAME}] Raw response was: {agent_response[:300]}")
        await _log_action(db, "parse_error", agent_response[:500], None)
        await db.commit()

    # --- AUTOMATED PIPELINE (background task) ---
    # Fire-and-forget: we don't await this.
    # The pipeline runs in the background while the HTTP response
    # returns immediately to the frontend.
    # The frontend's auto-refresh (every 30s) + delayed refreshes
    # will pick up assessments and deployments as they complete.
    if new_count > 0:
        print(f"[{AGENT_NAME}] 🔄 Starting background pipeline for {new_count} new disaster(s)...")
        asyncio.create_task(_run_automated_pipeline(db))

    result = {
        "status": "ok",
        "scanned_events": len(all_events),
        "new_disasters": new_count,
        "message": f"Scan complete. {new_count} new disaster(s) saved.",
    }
    await _log_action(db, "scan_complete", json.dumps(result), None)
    await db.commit()

    print(f"[{AGENT_NAME}] {result['message']}")
    return result


async def _log_action(db: AsyncSession, action: str, details: str, disaster_id):
    """Write an agent action to the audit log. Caller handles commit."""
    log = AgentLog(
        agent_name=AGENT_NAME,
        action=action,
        details=details,
        disaster_id=disaster_id,
    )
    db.add(log)


async def _run_automated_pipeline(db: AsyncSession):
    """
    Automatically runs AssessmentAgent then CoordinatorAgent
    right after MonitorAgent detects new disasters.

    WHY FRESH SESSIONS?
    We create a NEW database session for each sub-agent instead of
    passing the monitor's session down. This is because SQLAlchemy
    async sessions track their own transaction state. When you reuse
    the same session across multiple agents that each commit/flush,
    the session can get into a confused state — changes from one agent
    aren't visible to the next because they're in different
    transaction scopes.

    Fresh session per agent = clean slate, no cross-contamination.
    """
    from db.database import AsyncSessionLocal

    print(f"[{AGENT_NAME}] Pipeline step 1/2: Running AssessmentAgent...")
    try:
        from agents.assessment_agent import run_assessment_agent
        from db.models import DisasterStatus

        # Find all pending disasters using a FRESH session
        async with AsyncSessionLocal() as assessment_db:
            pending = await assessment_db.execute(
                select(Disaster).where(Disaster.status == DisasterStatus.DETECTED)
            )
            pending_disasters = pending.scalars().all()
            pending_ids = [d.id for d in pending_disasters]

        print(f"[{AGENT_NAME}] Found {len(pending_ids)} disaster(s) to assess")

        # Assess each one with its own fresh session
        assessed = 0
        for disaster_id in pending_ids:
            async with AsyncSessionLocal() as agent_db:
                result = await run_assessment_agent(agent_db, disaster_id=disaster_id)
                if result.get("assessed", 0) > 0:
                    assessed += 1
            await asyncio.sleep(2)  # respect Gemini rate limits

        print(f"[{AGENT_NAME}] Pipeline: {assessed}/{len(pending_ids)} disasters assessed")

    except Exception as e:
        print(f"[{AGENT_NAME}] Assessment pipeline error: {e}")

    await asyncio.sleep(3)

    print(f"[{AGENT_NAME}] Pipeline step 2/2: Running CoordinatorAgent...")
    try:
        from agents.coordinator_agent import run_coordinator_agent
        # Fresh session for coordinator too
        async with AsyncSessionLocal() as coord_db:
            result = await run_coordinator_agent(coord_db)
            print(f"[{AGENT_NAME}] Pipeline: {result.get('message', 'Coordination done')}")

    except Exception as e:
        print(f"[{AGENT_NAME}] Coordination pipeline error: {e}")

    print(f"[{AGENT_NAME}] ✅ Automated pipeline complete")
