"""
Disaster routes — CRUD + trigger agent pipeline.

GET  /api/disasters        → list all disasters
GET  /api/disasters/{id}   → get one disaster
POST /api/disasters        → manually create a disaster report
POST /api/disasters/scan   → trigger Monitor Agent to scan for new disasters
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from db.database import get_db
from db.models import Disaster, DisasterType, SeverityLevel, DisasterStatus

router = APIRouter()


# --- Pydantic Schemas ---

class DisasterCreate(BaseModel):
    title: str
    description: Optional[str] = None
    disaster_type: DisasterType
    severity: SeverityLevel
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    source: Optional[str] = "manual"


class DisasterResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    disaster_type: str
    severity: str
    status: str
    location_name: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    source: Optional[str]
    detected_at: datetime
    ai_assessment: Optional[str]

    class Config:
        from_attributes = True


# --- Routes ---

@router.get("/", response_model=list[DisasterResponse])
async def list_disasters(
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,
    limit: int = 50,
):
    query = select(Disaster).order_by(Disaster.detected_at.desc()).limit(limit)
    if status:
        query = query.where(Disaster.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{disaster_id}", response_model=DisasterResponse)
async def get_disaster(disaster_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Disaster).where(Disaster.id == disaster_id))
    disaster = result.scalar_one_or_none()
    if not disaster:
        raise HTTPException(status_code=404, detail="Disaster not found")
    return disaster


@router.post("/", response_model=DisasterResponse, status_code=201)
async def create_disaster(data: DisasterCreate, db: AsyncSession = Depends(get_db)):
    disaster = Disaster(**data.model_dump())
    db.add(disaster)
    await db.commit()
    await db.refresh(disaster)
    return disaster


@router.post("/scan")
async def trigger_scan(db: AsyncSession = Depends(get_db)):
    """
    Trigger the Monitor Agent to scan BMKG + GDACS right now.

    The scan itself runs synchronously (we wait for new disasters
    to be saved). But the automated pipeline (assess + coordinate)
    runs as a background task so this endpoint returns fast.

    Why background task?
    The full pipeline (15 disasters × Gemini calls) can take 2-5 minutes
    on HuggingFace's free CPU. If we await it here, the frontend times out
    at 30-120 seconds. Background task = instant response, dashboard
    auto-refreshes to pick up deployments as they come in.
    """
    from agents.monitor_agent import run_monitor_agent
    import asyncio

    result = await run_monitor_agent(db)
    return {
        **result,
        "pipeline": "running in background — dashboard will auto-refresh",
    }
