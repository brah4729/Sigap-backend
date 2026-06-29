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
# These define what JSON looks like coming IN and going OUT.
# Think of them as contracts between frontend and backend.

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
        from_attributes = True  # Allows converting SQLAlchemy models to this schema


# --- Routes ---

@router.get("/", response_model=list[DisasterResponse])
async def list_disasters(
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = None,  # ?status=DETECTED filter
    limit: int = 50,
):
    """
    List all disasters. Optionally filter by status.
    The frontend dashboard calls this to populate the map.
    """
    query = select(Disaster).order_by(Disaster.detected_at.desc()).limit(limit)

    if status:
        query = query.where(Disaster.status == status)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{disaster_id}", response_model=DisasterResponse)
async def get_disaster(disaster_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single disaster by ID."""
    result = await db.execute(select(Disaster).where(Disaster.id == disaster_id))
    disaster = result.scalar_one_or_none()

    if not disaster:
        raise HTTPException(status_code=404, detail="Disaster not found")

    return disaster


@router.post("/", response_model=DisasterResponse, status_code=201)
async def create_disaster(data: DisasterCreate, db: AsyncSession = Depends(get_db)):
    """
    Manually report a disaster (e.g., a field responder submits via mobile).
    The Assessment Agent will pick this up automatically.
    """
    disaster = Disaster(**data.model_dump())
    db.add(disaster)
    await db.commit()
    await db.refresh(disaster)
    return disaster


@router.post("/scan")
async def trigger_scan():
    """
    Trigger the Monitor Agent to scan BMKG and other sources right now.
    In production this runs on a schedule, but this endpoint lets
    judges trigger it manually for the demo.
    """
    # We'll wire this to the actual agent in Day 2
    return {
        "status": "triggered",
        "message": "Monitor Agent scan triggered. Check /api/disasters for new results.",
    }
