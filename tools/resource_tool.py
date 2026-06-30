"""
Resource Tool — helpers for querying and matching disaster response
resources (volunteers, medical teams, vehicles, etc.) to disasters.

Includes simple distance calculation (haversine formula) so the
Coordinator Agent can reason about which resources are physically
closest to a disaster.
"""

import math
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import Resource, ResourceType


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two points on Earth
    in kilometers. Used to find the nearest resources to a disaster.

    This is the standard formula for "as the crow flies" distance —
    not driving distance, but good enough for resource prioritization.
    """
    if None in (lat1, lon1, lat2, lon2):
        return float("inf")  # unknown distance = treat as very far

    R = 6371  # Earth's radius in km

    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return R * c


async def get_available_resources(db: AsyncSession) -> list[dict]:
    """Fetch all resources currently marked as available."""
    result = await db.execute(select(Resource).where(Resource.is_available == 1))
    resources = result.scalars().all()

    return [
        {
            "id": r.id,
            "name": r.name,
            "type": r.resource_type.value,
            "quantity": r.quantity,
            "location_name": r.location_name,
            "latitude": r.latitude,
            "longitude": r.longitude,
        }
        for r in resources
    ]


async def find_nearest_resources(
    db: AsyncSession,
    disaster_lat: float,
    disaster_lon: float,
    resource_type: str = None,
    limit: int = 10,
) -> list[dict]:
    """
    Find the nearest available resources to a disaster location,
    optionally filtered by resource type (VOLUNTEER, MEDICAL, etc).

    Returns resources sorted by distance, closest first.
    """
    query = select(Resource).where(Resource.is_available == 1)
    if resource_type:
        query = query.where(Resource.resource_type == ResourceType(resource_type))

    result = await db.execute(query)
    resources = result.scalars().all()

    scored = []
    for r in resources:
        distance = haversine_distance_km(disaster_lat, disaster_lon, r.latitude, r.longitude)
        scored.append({
            "id": r.id,
            "name": r.name,
            "type": r.resource_type.value,
            "quantity": r.quantity,
            "location_name": r.location_name,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "distance_km": round(distance, 1),
        })

    scored.sort(key=lambda x: x["distance_km"])
    return scored[:limit]


# --- Seed data ---
# Sample resources across Indonesia's major disaster-prone regions.
# In a real deployment this would come from a partner database
# (Red Cross, BNPB, local government). For the hackathon demo,
# we seed realistic starting data.

SEED_RESOURCES = [
    {"name": "BNPB Rapid Response Team Jakarta", "resource_type": ResourceType.MEDICAL, "quantity": 8, "location_name": "Jakarta", "latitude": -6.2088, "longitude": 106.8456},
    {"name": "PMI Volunteer Corps Surabaya", "resource_type": ResourceType.VOLUNTEER, "quantity": 50, "location_name": "Surabaya", "latitude": -7.2575, "longitude": 112.7521},
    {"name": "Medical Team Manado", "resource_type": ResourceType.MEDICAL, "quantity": 6, "location_name": "Manado", "latitude": 1.4748, "longitude": 124.8421},
    {"name": "Emergency Shelter Network Sulawesi", "resource_type": ResourceType.SHELTER, "quantity": 2000, "location_name": "Palu", "latitude": -0.8917, "longitude": 119.8707},
    {"name": "Food Bank Bengkulu", "resource_type": ResourceType.FOOD, "quantity": 5000, "location_name": "Bengkulu", "latitude": -3.7928, "longitude": 102.2608},
    {"name": "Rescue Vehicle Fleet NTT", "resource_type": ResourceType.VEHICLE, "quantity": 15, "location_name": "Kupang", "latitude": -10.1772, "longitude": 123.6070},
    {"name": "PMI Volunteer Corps Sangihe", "resource_type": ResourceType.VOLUNTEER, "quantity": 30, "location_name": "Tahuna", "latitude": 3.6177, "longitude": 125.4975},
    {"name": "Medical Team Pacitan", "resource_type": ResourceType.MEDICAL, "quantity": 4, "location_name": "Pacitan", "latitude": -8.1936, "longitude": 111.0992},
    {"name": "Shelter Network Alor", "resource_type": ResourceType.SHELTER, "quantity": 800, "location_name": "Alor", "latitude": -8.2333, "longitude": 124.7500},
    {"name": "Vehicle Fleet Bitung", "resource_type": ResourceType.VEHICLE, "quantity": 10, "location_name": "Bitung", "latitude": 1.4451, "longitude": 125.1815},
]


async def seed_resources(db: AsyncSession) -> int:
    """
    Insert sample resources into the database if none exist yet.
    Returns the number of resources created.
    """
    existing = await db.execute(select(Resource).limit(1))
    if existing.scalar_one_or_none():
        return 0  # already seeded, don't duplicate

    for data in SEED_RESOURCES:
        resource = Resource(**data, is_available=1)
        db.add(resource)

    await db.commit()
    return len(SEED_RESOURCES)
