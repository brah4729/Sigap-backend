"""
Monitor Agent — the "eyes" of SIGAP.

Responsibilities:
  - Periodically scan BMKG and GDACS for new disaster events
  - Deduplicate events (don't save the same quake twice)
  - Save new disasters to the database
  - Log every action for transparency
  - Hand off to Assessment Agent when something significant is found

Google ADK concepts used here:
  - Agent: the AI brain that decides what to do
  - Tool: a function the agent can call (our BMKG fetcher)
  - Runner: executes the agent with a given input
"""

import json
import asyncio
from datetime import datetime, timezone

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google import genai

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import Disaster, AgentLog, DisasterType, SeverityLevel, DisasterStatus
from tools.bmkg_tool import (
    fetch_latest_earthquakes,
    fetch_latest_earthquake_single,
    fetch_disaster_rss,
    calculate_severity,
)
from dotenv import load_dotenv
import os

load_dotenv()

# --- ADK Client Setup ---
# genai.Client connects to Google's AI services using your API key
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

MODEL = "gemini-2.0-flash"  # Fast model — good for monitoring tasks
AGENT_NAME = "MonitorAgent"


# --- ADK Tool Functions ---
# These are the functions the AI agent can "call" during reasoning.
# ADK agents work by deciding which tool to use, calling it,
# reading the result, then deciding the next step — like a loop.

async def tool_fetch_earthquakes() -> str:
    """Fetch latest earthquake data from BMKG API."""
    earthquakes = await fetch_latest_earthquakes()
    if not earthquakes:
        return json.dumps({"status": "no_data", "earthquakes": []})
    return json.dumps({
        "status": "ok",
        "count": len(earthquakes),
        "earthquakes": earthquakes[:5],  # limit to 5 for agent context
    })


async def tool_fetch_latest_single_quake() -> str:
    """Fetch only the most recent significant earthquake from BMKG."""
    quake = await fetch_latest_earthquake_single()
    if not quake:
        return json.dumps({"status": "no_data", "earthquake": None})
    return json.dumps({"status": "ok", "earthquake": quake})


async def tool_fetch_rss_alerts() -> str:
    """Fetch international disaster alerts from GDACS RSS feed."""
    alerts = await fetch_disaster_rss()
    if not alerts:
        return json.dumps({"status": "no_data", "alerts": []})
    return json.dumps({
        "status": "ok",
        "count": len(alerts),
        "alerts": alerts,
    })


# --- Core Monitor Logic ---

async def run_monitor_agent(db: AsyncSession) -> dict:
    """
    Main entry point — runs the Monitor Agent pipeline.

    Flow:
      1. Create ADK agent with our tools
      2. Ask it to scan for disasters
      3. Parse its response
      4. Save new disasters to DB
      5. Return summary

    This is called by:
      - POST /api/disasters/scan (manual trigger)
      - Background scheduler (every 10 minutes in production)
    """

    # Step 1: Fetch raw data directly (faster than going through agent for data fetch)
    # We use the agent for ANALYSIS, not just data fetching
    print(f"[{AGENT_NAME}] Starting disaster scan...")

    earthquakes = await fetch_latest_earthquakes()
    rss_alerts = await fetch_disaster_rss()

    all_events = earthquakes + rss_alerts
    print(f"[{AGENT_NAME}] Found {len(all_events)} raw events from sources")

    if not all_events:
        await _log_action(db, "scan_complete", "No events found from any source", None)
        return {"status": "ok", "new_disasters": 0, "message": "No new events found"}

    # Step 2: Use ADK Agent to analyze which events are significant
    agent = Agent(
        name=AGENT_NAME,
        model=MODEL,
        description="Disaster monitoring agent for Indonesia. Analyzes raw disaster data and identifies significant events requiring response.",
        instruction="""
        You are a disaster monitoring agent for Indonesia.
        
        You will receive raw disaster event data from BMKG and GDACS.
        Your job is to:
        1. Identify which events are SIGNIFICANT (magnitude >= 5.0 for earthquakes, or any flood/tsunami/volcano)
        2. For each significant event, determine:
           - severity: LOW, MEDIUM, HIGH, or CRITICAL
           - whether it needs immediate response
        3. Return a JSON list of significant events with this exact structure:
        
        {
          "significant_events": [
            {
              "title": "event title",
              "type": "EARTHQUAKE|FLOOD|TSUNAMI|VOLCANO|LANDSLIDE|OTHER",
              "severity": "LOW|MEDIUM|HIGH|CRITICAL",
              "location_name": "location",
              "latitude": 0.0,
              "longitude": 0.0,
              "source": "BMKG|GDACS",
              "description": "brief description of why this is significant",
              "needs_immediate_response": true/false
            }
          ]
        }
        
        Return ONLY the JSON. No markdown, no explanation.
        """,
    )

    # Prepare the input for the agent — summarize the raw events
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
        ]
    })

    # Step 3: Run the agent
    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="sigap", user_id="system", session_id="monitor_scan"
    )

    runner = Runner(agent=agent, app_name="sigap", session_service=session_service)

    agent_response = ""
    async for event in runner.run_async(
        user_id="system",
        session_id="monitor_scan",
        new_message=f"Analyze these disaster events and identify significant ones:\n{events_summary}",
    ):
        if event.is_final_response():
            agent_response = event.content.parts[0].text
            break

    print(f"[{AGENT_NAME}] Agent response received")

    # Step 4: Parse agent response and save to DB
    new_count = 0
    try:
        # Strip any accidental markdown code fences
        clean_response = agent_response.strip().strip("```json").strip("```").strip()
        parsed = json.loads(clean_response)
        significant_events = parsed.get("significant_events", [])

        for event in significant_events:
            # Check if this disaster already exists (deduplication)
            # We match on title + location to avoid duplicates
            existing = await db.execute(
                select(Disaster).where(
                    Disaster.title == event["title"],
                    Disaster.location_name == event.get("location_name"),
                )
            )
            if existing.scalar_one_or_none():
                print(f"[{AGENT_NAME}] Skipping duplicate: {event['title']}")
                continue

            # Save new disaster to DB
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
                ai_assessment=f"Detected by MonitorAgent. Needs immediate response: {event.get('needs_immediate_response', False)}",
            )
            db.add(disaster)
            await db.flush()  # flush to get the ID before commit

            # Log the action
            await _log_action(
                db,
                "disaster_detected",
                json.dumps(event),
                disaster.id,
            )
            new_count += 1
            print(f"[{AGENT_NAME}] Saved new disaster: {event['title']}")

        await db.commit()

    except json.JSONDecodeError as e:
        print(f"[{AGENT_NAME}] Failed to parse agent response: {e}")
        print(f"[{AGENT_NAME}] Raw response: {agent_response}")
        await _log_action(db, "parse_error", f"Failed to parse: {agent_response[:200]}", None)

    result = {
        "status": "ok",
        "scanned_events": len(all_events),
        "new_disasters": new_count,
        "message": f"Scan complete. {new_count} new disaster(s) saved.",
    }

    await _log_action(db, "scan_complete", json.dumps(result), None)
    print(f"[{AGENT_NAME}] {result['message']}")
    return result


# --- Helper ---

async def _log_action(db: AsyncSession, action: str, details: str, disaster_id):
    """Save an agent action to the audit log."""
    log = AgentLog(
        agent_name=AGENT_NAME,
        action=action,
        details=details,
        disaster_id=disaster_id,
    )
    db.add(log)
    # Don't commit here — caller handles commit
