"""
Resource routes — manage and view disaster response resources
(volunteers, medical teams, vehicles, shelters, food).

POST /api/resources/seed       → populate sample resources (run once)
GET  /api/resources/           → list all resources
GET  /api/resources/deployments → list all resource deployments
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.database import get_db
from db.models import Resource, ResourceDeployment, Disaster
from tools.resource_tool import seed_resources

router = APIRouter()


@router.post("/seed")
async def seed(db: AsyncSession = Depends(get_db)):
    """
    Seed the database with sample resources across Indonesia.
    Safe to call multiple times — won't duplicate if resources already exist.
    """
    count = await seed_resources(db)
    if count == 0:
        return {"status": "skipped", "message": "Resources already seeded"}
    return {"status": "ok", "seeded": count, "message": f"Seeded {count} resources"}


@router.get("/")
async def list_resources(db: AsyncSession = Depends(get_db)):
    """List all resources with their current availability."""
    result = await db.execute(select(Resource))
    resources = result.scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "type": r.resource_type.value,
            "quantity": r.quantity,
            "location": r.location_name,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "is_available": bool(r.is_available),
        }
        for r in resources
    ]


@router.get("/deployments")
async def list_deployments(db: AsyncSession = Depends(get_db)):
    """
    List all resource deployments — shows which resources went
    to which disasters and why. This is the 'proof of coordination'
    for the Kaggle demo.
    """
    result = await db.execute(
        select(ResourceDeployment)
        .options(
            selectinload(ResourceDeployment.disaster),
            selectinload(ResourceDeployment.resource),
        )
        .order_by(ResourceDeployment.deployed_at.desc())
    )
    deployments = result.scalars().all()

    return [
        {
            "id": d.id,
            "disaster_title": d.disaster.title if d.disaster else None,
            "resource_name": d.resource.name if d.resource else None,
            "resource_type": d.resource.resource_type.value if d.resource else None,
            "notes": d.notes,
            "deployed_at": d.deployed_at,
        }
        for d in deployments
    ]
