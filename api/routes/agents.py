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


@router.get("/key-status")
async def key_status():
    """
    Check which API keys are configured without exposing the actual key values.
    Useful for debugging quota issues.
    """
    from config import get_all_key_status
    return get_all_key_status()


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


@router.post("/cleanup")
async def manual_cleanup(
    db: AsyncSession = Depends(get_db),
    older_than_hours: int = 3,
):
    """
    Manually trigger data cleanup.
    Deletes disasters older than `older_than_hours` (default: 3).
    Then runs a fresh monitor scan to repopulate.
    """
    from scheduler import cleanup_old_data, run_fresh_scan
    cleaned = await cleanup_old_data(older_than_hours=older_than_hours)
    if cleaned > 0:
        await run_fresh_scan()
    return {
        "status": "ok",
        "cleaned": cleaned,
        "message": f"Removed {cleaned} old disaster(s). Fresh scan triggered.",
    }


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

    if agent_name == "orchestrator":
        from agents.orchestrator_agent import run_orchestrator_agent
        result = await run_orchestrator_agent(db)
        return result

    return {"error": f"Unknown agent '{agent_name}'"}
