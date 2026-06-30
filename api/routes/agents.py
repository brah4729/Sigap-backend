"""
Agent control routes — trigger agents and view their logs.

POST /api/agents/run/{agent_name}   → manually trigger an agent
GET  /api/agents/logs               → view agent action history
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from db.database import get_db
from db.models import AgentLog

router = APIRouter()


@router.get("/logs")
async def get_agent_logs(
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
):
    """
    Returns the audit log of all agent actions.
    This is key for the Kaggle demo — shows judges what agents did.
    """
    result = await db.execute(
        select(AgentLog).order_by(AgentLog.created_at.desc()).limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "agent": log.agent_name,
            "action": log.action,
            "details": log.details,
            "disaster_id": log.disaster_id,
            "timestamp": log.created_at,
        }
        for log in logs
    ]


@router.post("/run/{agent_name}")
async def run_agent(
    agent_name: str,
    db: AsyncSession = Depends(get_db),
    disaster_id: Optional[int] = None,
):
    """
    Manually trigger a specific agent by name.
    Valid names: monitor, assessment, coordinator, orchestrator

    For 'assessment', you can optionally pass ?disaster_id=5
    to assess one specific disaster instead of all pending ones.
    """
    valid_agents = ["monitor", "assessment", "coordinator", "orchestrator"]

    if agent_name not in valid_agents:
        return {
            "error": f"Unknown agent '{agent_name}'",
            "valid_agents": valid_agents,
        }

    # Import here to avoid circular imports at module load time
    if agent_name == "monitor":
        from agents.monitor_agent import run_monitor_agent
        result = await run_monitor_agent(db)
        return result

    if agent_name == "assessment":
        from agents.assessment_agent import run_assessment_agent
        result = await run_assessment_agent(db, disaster_id=disaster_id)
        return result

    if agent_name == "coordinator":
        from agents.coordinator_agent import run_coordinator_agent
        result = await run_coordinator_agent(db, disaster_id=disaster_id)
        return result

    # orchestrator comes next
    return {
        "status": "not_implemented",
        "agent": agent_name,
        "message": f"{agent_name.capitalize()} Agent is coming soon.",
    }
