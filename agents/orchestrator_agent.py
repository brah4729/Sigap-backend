"""
Orchestrator Agent — the "brain" that coordinates all other agents.

This is the top-level agent in SIGAP's multi-agent system.

Architecture:
  - Queries live disaster data via MCP tool functions
  - Uses Gemini to reason about the overall situation
  - Produces a structured situation report
  - Triggers sub-agents as needed

Why we call MCP tools directly instead of via ADK's MCPToolset:
  ADK 2.x broke the MCPToolset.from_server() API that existed in 1.x.
  Our MCP server (disaster_mcp_server.py) still runs as a proper
  standalone MCP server for external clients — but internally we
  call the same tool logic directly for reliability.

  This is a real-world pattern: internal callers use direct imports,
  external callers use the MCP protocol. Same tools, two interfaces.
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

from db.models import Disaster, Resource, AgentLog
from config import get_api_key, use_api_key_for, GEMINI_MODEL
from dotenv import load_dotenv
import os

load_dotenv()

MODEL = GEMINI_MODEL
AGENT_NAME = "OrchestratorAgent"


# --- MCP Tool Functions ---
# These are the SAME tools exposed by disaster_mcp_server.py,
# called directly as Python functions here.
# The MCP server wraps these same queries for external clients.

async def mcp_get_active_disasters(db: AsyncSession, severity_filter: str = None, limit: int = 20) -> dict:
    """MCP tool: get_active_disasters — fetch current disaster events."""
    from db.models import SeverityLevel

    query = select(Disaster).order_by(Disaster.detected_at.desc()).limit(limit)
    if severity_filter:
        query = query.where(Disaster.severity == SeverityLevel(severity_filter))

    result = await db.execute(query)
    disasters = result.scalars().all()

    return {
        "tool": "get_active_disasters",
        "count": len(disasters),
        "disasters": [
            {
                "id": d.id,
                "title": d.title,
                "type": d.disaster_type.value,
                "severity": d.severity.value,
                "status": d.status.value,
                "location": d.location_name,
                "latitude": d.latitude,
                "longitude": d.longitude,
                "source": d.source,
            }
            for d in disasters
        ],
    }


async def mcp_get_resource_status(db: AsyncSession, available_only: bool = False) -> dict:
    """MCP tool: get_resource_status — check available response resources."""
    query = select(Resource)
    if available_only:
        query = query.where(Resource.is_available == 1)

    result = await db.execute(query)
    resources = result.scalars().all()

    data = [
        {
            "id": r.id,
            "name": r.name,
            "type": r.resource_type.value,
            "quantity": r.quantity,
            "location": r.location_name,
            "is_available": bool(r.is_available),
        }
        for r in resources
    ]

    available_count = sum(1 for r in data if r["is_available"])

    return {
        "tool": "get_resource_status",
        "total_resources": len(data),
        "available": available_count,
        "deployed": len(data) - available_count,
        "resources": data,
    }


async def run_orchestrator_agent(db: AsyncSession) -> dict:
    """
    Run the full orchestration pipeline:
    1. Call MCP tools to gather current situation data
    2. Feed data to Gemini for analysis
    3. Return structured situation report
    """
    print(f"[{AGENT_NAME}] Starting orchestration...")

    # Use orchestrator's specific API key
    use_api_key_for("orchestrator")

    # Step 1: Call MCP tools to gather data
    # (same data the MCP server exposes to external agents)
    print(f"[{AGENT_NAME}] Calling MCP tool: get_active_disasters...")
    disasters_data = await mcp_get_active_disasters(db, limit=20)

    print(f"[{AGENT_NAME}] Calling MCP tool: get_resource_status...")
    resources_data = await mcp_get_resource_status(db)

    # Step 2: Build the Gemini agent
    agent = Agent(
        name=AGENT_NAME,
        model=MODEL,
        description="Top-level orchestrator for SIGAP disaster response system.",
        instruction="""
You are the orchestrator of SIGAP — Indonesia's AI disaster response system.

You will receive real-time data from the SIGAP MCP tools including:
- Active disaster events (from get_active_disasters tool)
- Resource availability (from get_resource_status tool)

Your job:
1. Analyze the overall disaster situation across Indonesia
2. Identify the most critical events requiring immediate attention
3. Assess current response capacity based on available resources
4. Produce a clear situation report for emergency coordinators

Structure your report as:
- SITUATION OVERVIEW: summary of active disasters
- CRITICAL EVENTS: which need immediate attention and why
- RESOURCE CAPACITY: available vs deployed resources assessment
- RECOMMENDED ACTIONS: specific next steps (numbered list)

End your response with ONLY this JSON block (no extra text after):
{
  "situation_level": "NORMAL or ELEVATED or CRITICAL",
  "active_disasters": <total number>,
  "critical_count": <number of HIGH or CRITICAL severity>,
  "recommended_actions": ["action 1", "action 2", "action 3"]
}
""",
    )

    # Step 3: Feed the MCP data into the agent as context
    # We combine both tool results into one message
    context = json.dumps({
        "mcp_tool_results": {
            "get_active_disasters": disasters_data,
            "get_resource_status": resources_data,
        }
    }, ensure_ascii=False, indent=2)

    user_message = types.Content(
        role="user",
        parts=[types.Part(
            text=f"Here is the live data from SIGAP MCP tools. Analyze and produce a situation report:\n\n{context}"
        )],
    )

    # Step 4: Run the agent with retry
    print(f"[{AGENT_NAME}] Running Gemini analysis...")
    situation_report = await _run_with_retry(agent, user_message)

    if not situation_report:
        return {
            "status": "error",
            "message": "Gemini temporarily unavailable. MCP data collected successfully.",
            "raw_data": {
                "disasters": disasters_data,
                "resources": resources_data,
            },
        }

    # Step 5: Try to extract the JSON summary from the end of the report
    summary = {}
    try:
        # Find the last { ... } block in the response
        last_brace = situation_report.rfind("{")
        last_close = situation_report.rfind("}")
        if last_brace != -1 and last_close != -1:
            json_str = situation_report[last_brace:last_close + 1]
            summary = json.loads(json_str)
    except json.JSONDecodeError:
        pass  # summary stays empty, that's fine

    # Log the orchestration
    await _log_action(db, "orchestration_complete", situation_report[:500], None)
    await db.commit()

    print(f"[{AGENT_NAME}] ✅ Orchestration complete")
    print(f"[{AGENT_NAME}] Situation level: {summary.get('situation_level', 'unknown')}")

    return {
        "status": "ok",
        "situation_level": summary.get("situation_level", "UNKNOWN"),
        "active_disasters": summary.get("active_disasters", disasters_data["count"]),
        "critical_count": summary.get("critical_count", 0),
        "recommended_actions": summary.get("recommended_actions", []),
        "full_report": situation_report,
        "mcp_data_summary": {
            "disasters_fetched": disasters_data["count"],
            "resources_available": resources_data["available"],
            "resources_deployed": resources_data["deployed"],
        },
    }


async def _run_with_retry(agent: Agent, user_message: types.Content, max_retries: int = 3) -> str:
    """Run ADK agent with exponential backoff on 503 errors."""
    for attempt in range(1, max_retries + 1):
        try:
            session_service = InMemorySessionService()
            await session_service.create_session(
                app_name="sigap", user_id="system", session_id=f"orchestrator_{attempt}"
            )
            runner = Runner(agent=agent, app_name="sigap", session_service=session_service)

            response_text = ""
            async for event in runner.run_async(
                user_id="system",
                session_id=f"orchestrator_{attempt}",
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
            print(f"[{AGENT_NAME}] Client error: {e}")
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
