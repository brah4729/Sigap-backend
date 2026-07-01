"""
Assessment Agent — the "brain" of SIGAP.

Takes disasters detected by MonitorAgent and performs deep analysis:
  - Estimates affected population based on location + magnitude
  - Recommends specific response actions (evacuation, medical, etc.)
  - Refines severity level with more context
  - Updates disaster status from DETECTED → ASSESSING → RESPONDING

This is where Gemini does real reasoning, not just classification.
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

from db.models import Disaster, AgentLog, DisasterStatus, SeverityLevel
from config import get_api_key, GEMINI_MODEL
from dotenv import load_dotenv
import os

load_dotenv()

MODEL = GEMINI_MODEL
AGENT_NAME = "AssessmentAgent"


async def run_assessment_agent(db: AsyncSession, disaster_id: int = None) -> dict:
    """
    Run assessment on unassessed disasters.

    If disaster_id is given → assess only that one disaster.
    If None → assess ALL disasters with status DETECTED.

    Returns a summary of what was assessed.
    """
    print(f"[{AGENT_NAME}] Starting assessment run...")

    # Use this agent's specific API key
    import google.genai as genai
    genai.configure(api_key=get_api_key("assessment"))

    # Fetch disasters that need assessment
    if disaster_id:
        query = select(Disaster).where(Disaster.id == disaster_id)
    else:
        # Get all newly detected disasters not yet assessed
        query = select(Disaster).where(
            Disaster.status == DisasterStatus.DETECTED
        ).limit(5)  # process 5 at a time to save API quota

    result = await db.execute(query)
    disasters = result.scalars().all()

    if not disasters:
        print(f"[{AGENT_NAME}] No disasters pending assessment")
        return {"status": "ok", "assessed": 0, "message": "No disasters pending assessment"}

    print(f"[{AGENT_NAME}] Found {len(disasters)} disaster(s) to assess")

    # Build the agent
    agent = Agent(
        name=AGENT_NAME,
        model=MODEL,
        description="Disaster assessment specialist for Indonesia. Provides detailed impact analysis and response recommendations.",
        instruction="""
You are an expert disaster assessment specialist for Indonesia.

Given a disaster event, provide a thorough assessment including:
1. Estimated affected population (based on location and magnitude)
2. Immediate risks (aftershocks, tsunami, infrastructure damage, etc.)
3. Recommended response actions (be specific and prioritized)
4. Required resources (medical teams, evacuation vehicles, food, etc.)
5. Refined severity: LOW, MEDIUM, HIGH, or CRITICAL
6. Estimated response time needed

Return ONLY this JSON structure (no markdown, no explanation):
{
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "affected_population_estimate": "e.g. 50,000 - 100,000 people",
  "immediate_risks": ["risk 1", "risk 2"],
  "recommended_actions": [
    {"priority": 1, "action": "Deploy search and rescue to...", "timeframe": "0-2 hours"},
    {"priority": 2, "action": "Set up emergency shelter at...", "timeframe": "2-6 hours"}
  ],
  "required_resources": {
    "medical_teams": 5,
    "evacuation_vehicles": 20,
    "food_packages": 10000,
    "shelter_capacity_needed": 5000
  },
  "estimated_response_hours": 24,
  "assessment_summary": "2-3 sentence summary for the dashboard"
}
""",
    )

    assessed_count = 0

    for disaster in disasters:
        print(f"[{AGENT_NAME}] Assessing: {disaster.title}")

        # Mark as ASSESSING so we don't double-process
        disaster.status = DisasterStatus.ASSESSING
        await db.commit()

        # Build context for this specific disaster
        disaster_context = json.dumps({
            "title": disaster.title,
            "type": disaster.disaster_type.value,
            "current_severity": disaster.severity.value,
            "location": disaster.location_name,
            "latitude": disaster.latitude,
            "longitude": disaster.longitude,
            "source": disaster.source,
            "description": disaster.description,
            "initial_assessment": disaster.ai_assessment,
        })

        user_message = types.Content(
            role="user",
            parts=[types.Part(
                text=f"Assess this disaster event and provide detailed analysis:\n{disaster_context}"
            )],
        )

        # Run agent with retry
        assessment_json = await _run_with_retry(agent, user_message)

        if not assessment_json:
            # If agent failed, keep ASSESSING status and move on
            print(f"[{AGENT_NAME}] Failed to assess {disaster.title}, skipping")
            await _log_action(db, "assessment_failed", f"Could not assess disaster {disaster.id}", disaster.id)
            await db.commit()
            continue

        try:
            # Parse the assessment
            clean = assessment_json.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1]).strip()

            assessment = json.loads(clean)

            # Update the disaster with assessment results
            disaster.severity = SeverityLevel(assessment.get("severity", disaster.severity.value))
            disaster.status = DisasterStatus.RESPONDING
            disaster.ai_assessment = json.dumps({
                "summary": assessment.get("assessment_summary", ""),
                "affected_population": assessment.get("affected_population_estimate", "Unknown"),
                "immediate_risks": assessment.get("immediate_risks", []),
                "recommended_actions": assessment.get("recommended_actions", []),
                "required_resources": assessment.get("required_resources", {}),
                "estimated_response_hours": assessment.get("estimated_response_hours", 24),
                "assessed_by": AGENT_NAME,
            })

            await db.commit()

            await _log_action(
                db,
                "assessment_complete",
                f"Severity: {disaster.severity.value} | {assessment.get('assessment_summary', '')}",
                disaster.id,
            )
            await db.commit()

            assessed_count += 1
            print(f"[{AGENT_NAME}] ✅ Assessed: {disaster.title} → {disaster.severity.value}")

        except (json.JSONDecodeError, ValueError) as e:
            print(f"[{AGENT_NAME}] Parse error for {disaster.title}: {e}")
            await _log_action(db, "parse_error", f"Raw: {assessment_json[:200]}", disaster.id)
            await db.commit()

        # Small delay between API calls to avoid rate limiting
        await asyncio.sleep(1)

    result = {
        "status": "ok",
        "assessed": assessed_count,
        "message": f"Assessment complete. {assessed_count} disaster(s) assessed.",
    }
    print(f"[{AGENT_NAME}] {result['message']}")
    return result


async def _run_with_retry(agent: Agent, user_message: types.Content, max_retries: int = 3) -> str:
    """
    Run the ADK agent with exponential backoff retry.
    Returns the agent's text response, or empty string on failure.
    """
    for attempt in range(1, max_retries + 1):
        try:
            session_service = InMemorySessionService()
            await session_service.create_session(
                app_name="sigap", user_id="system", session_id=f"assessment_{attempt}"
            )
            runner = Runner(
                agent=agent,
                app_name="sigap",
                session_service=session_service,
            )

            response_text = ""
            async for event in runner.run_async(
                user_id="system",
                session_id=f"assessment_{attempt}",
                new_message=user_message,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    response_text = event.content.parts[0].text

            return response_text

        except ServerError as e:
            if attempt < max_retries:
                wait = 2 ** attempt
                print(f"[{AGENT_NAME}] 503 on attempt {attempt}, retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                print(f"[{AGENT_NAME}] All retries failed: {e}")
                return ""

        except ClientError as e:
            print(f"[{AGENT_NAME}] Client error (quota?): {e}")
            return ""

    return ""


async def _log_action(db: AsyncSession, action: str, details: str, disaster_id):
    """Write an agent action to the audit log."""
    log = AgentLog(
        agent_name=AGENT_NAME,
        action=action,
        details=details,
        disaster_id=disaster_id,
    )
    db.add(log)
