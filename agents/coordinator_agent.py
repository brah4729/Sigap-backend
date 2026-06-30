"""
Coordinator Agent — the "hands" of SIGAP.

Takes disasters that have been assessed (status=RESPONDING) and
decides which resources to deploy:
  - Finds nearest available resources by type and distance
  - Uses Gemini to reason about allocation priority and quantity
  - Creates ResourceDeployment records
  - Marks resources as partially/fully committed
  - Updates disaster status to RESOLVED once response is dispatched

This agent demonstrates real decision-making: it doesn't just pick
the closest resource, it reasons about WHAT KIND and HOW MUCH is
needed based on the Assessment Agent's analysis.
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

from db.models import Disaster, Resource, ResourceDeployment, AgentLog, DisasterStatus
from tools.resource_tool import find_nearest_resources
from dotenv import load_dotenv
import os

load_dotenv()

MODEL = "gemini-2.5-flash-lite"
AGENT_NAME = "CoordinatorAgent"


async def run_coordinator_agent(db: AsyncSession, disaster_id: int = None) -> dict:
    """
    Run resource coordination on assessed disasters.

    If disaster_id is given → coordinate only that one disaster.
    If None → coordinate ALL disasters with status RESPONDING.
    """
    print(f"[{AGENT_NAME}] Starting coordination run...")

    if disaster_id:
        query = select(Disaster).where(Disaster.id == disaster_id)
    else:
        query = select(Disaster).where(
            Disaster.status == DisasterStatus.RESPONDING
        ).limit(5)  # batch limit to save API quota

    result = await db.execute(query)
    disasters = result.scalars().all()

    if not disasters:
        print(f"[{AGENT_NAME}] No disasters pending coordination")
        return {"status": "ok", "coordinated": 0, "message": "No disasters pending coordination"}

    print(f"[{AGENT_NAME}] Found {len(disasters)} disaster(s) to coordinate")

    agent = Agent(
        name=AGENT_NAME,
        model=MODEL,
        description="Resource coordination specialist for disaster response in Indonesia.",
        instruction="""
You are a resource coordination specialist for disaster response.

Given a disaster's assessment data and a list of nearby available resources,
decide which resources to deploy and how much of each.

Consider:
- Distance (closer is better, but not the only factor)
- Resource type matching the disaster's needs
- Available quantity vs. what's actually needed
- Severity — CRITICAL/HIGH disasters get priority allocation

Return ONLY this JSON (no markdown, no explanation):
{
  "deployments": [
    {
      "resource_id": 3,
      "resource_name": "Medical Team Pacitan",
      "quantity_deployed": 4,
      "reasoning": "Closest medical team, matches the medical need from assessment"
    }
  ],
  "coordination_summary": "2-3 sentence summary of the response plan"
}

If no resources are suitable or available, return {"deployments": [], "coordination_summary": "explanation"}
""",
    )

    coordinated_count = 0

    for disaster in disasters:
        print(f"[{AGENT_NAME}] Coordinating: {disaster.title}")

        # Parse the assessment to know what resources are needed
        try:
            assessment = json.loads(disaster.ai_assessment) if disaster.ai_assessment else {}
        except json.JSONDecodeError:
            assessment = {}

        # Find nearby available resources (no type filter — let the AI decide)
        nearby_resources = await find_nearest_resources(
            db,
            disaster_lat=disaster.latitude,
            disaster_lon=disaster.longitude,
            resource_type=None,
            limit=8,
        )

        if not nearby_resources:
            print(f"[{AGENT_NAME}] No resources found near {disaster.title}")
            await _log_action(db, "no_resources_available", "No nearby resources found", disaster.id)
            await db.commit()
            continue

        context = json.dumps({
            "disaster": {
                "title": disaster.title,
                "type": disaster.disaster_type.value,
                "severity": disaster.severity.value,
                "location": disaster.location_name,
            },
            "assessment": assessment,
            "nearby_resources": nearby_resources,
        })

        user_message = types.Content(
            role="user",
            parts=[types.Part(text=f"Decide resource deployment for this disaster:\n{context}")],
        )

        response_text = await _run_with_retry(agent, user_message)

        if not response_text:
            print(f"[{AGENT_NAME}] Failed to get coordination plan for {disaster.title}")
            await _log_action(db, "coordination_failed", "Agent call failed", disaster.id)
            await db.commit()
            continue

        try:
            clean = response_text.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:-1]).strip()

            plan = json.loads(clean)
            deployments = plan.get("deployments", [])

            for dep in deployments:
                resource_id = dep.get("resource_id")
                resource = await db.get(Resource, resource_id)
                if not resource:
                    continue

                # Create the deployment record
                deployment = ResourceDeployment(
                    disaster_id=disaster.id,
                    resource_id=resource_id,
                    notes=dep.get("reasoning", ""),
                )
                db.add(deployment)

                # Mark resource as no longer fully available
                # (simple model: any deployment makes it unavailable for new disasters)
                resource.is_available = 0

            # Update disaster status — response has been dispatched
            disaster.status = DisasterStatus.RESOLVED
            disaster.ai_assessment = json.dumps({
                **assessment,
                "coordination": {
                    "summary": plan.get("coordination_summary", ""),
                    "deployments": deployments,
                    "coordinated_by": AGENT_NAME,
                },
            })

            await db.commit()

            await _log_action(
                db,
                "coordination_complete",
                plan.get("coordination_summary", ""),
                disaster.id,
            )
            await db.commit()

            coordinated_count += 1
            print(f"[{AGENT_NAME}] ✅ Coordinated: {disaster.title} ({len(deployments)} resources deployed)")

        except (json.JSONDecodeError, ValueError) as e:
            print(f"[{AGENT_NAME}] Parse error for {disaster.title}: {e}")
            await _log_action(db, "parse_error", response_text[:200], disaster.id)
            await db.commit()

        await asyncio.sleep(1)  # avoid rate limiting

    result = {
        "status": "ok",
        "coordinated": coordinated_count,
        "message": f"Coordination complete. {coordinated_count} disaster(s) had resources deployed.",
    }
    print(f"[{AGENT_NAME}] {result['message']}")
    return result


async def _run_with_retry(agent: Agent, user_message: types.Content, max_retries: int = 3) -> str:
    """Run the ADK agent with exponential backoff retry on 503 errors."""
    for attempt in range(1, max_retries + 1):
        try:
            session_service = InMemorySessionService()
            await session_service.create_session(
                app_name="sigap", user_id="system", session_id=f"coordinator_{attempt}"
            )
            runner = Runner(agent=agent, app_name="sigap", session_service=session_service)

            response_text = ""
            async for event in runner.run_async(
                user_id="system",
                session_id=f"coordinator_{attempt}",
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
