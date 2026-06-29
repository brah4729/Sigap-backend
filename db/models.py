"""
SQLAlchemy database models for SIGAP.

These define the tables in our SQLite database.
Each class = one table.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Enum, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import enum

from db.database import Base


# --- Enums ---
# Using Python enums keeps our data consistent —
# no typos like "HIGHT" instead of "HIGH"

class SeverityLevel(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DisasterType(str, enum.Enum):
    EARTHQUAKE = "EARTHQUAKE"
    FLOOD = "FLOOD"
    TSUNAMI = "TSUNAMI"
    VOLCANO = "VOLCANO"
    LANDSLIDE = "LANDSLIDE"
    FIRE = "FIRE"
    OTHER = "OTHER"


class DisasterStatus(str, enum.Enum):
    DETECTED = "DETECTED"       # Just found by monitor agent
    ASSESSING = "ASSESSING"     # Assessment agent working on it
    RESPONDING = "RESPONDING"   # Coordinator agent deployed resources
    RESOLVED = "RESOLVED"       # Situation handled


class ResourceType(str, enum.Enum):
    VOLUNTEER = "VOLUNTEER"
    MEDICAL = "MEDICAL"
    FOOD = "FOOD"
    SHELTER = "SHELTER"
    VEHICLE = "VEHICLE"


# --- Models ---

class Disaster(Base):
    """
    Represents a disaster event detected by the Monitor Agent.
    This is the central table — everything else references it.
    """
    __tablename__ = "disasters"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    disaster_type = Column(Enum(DisasterType), nullable=False)
    severity = Column(Enum(SeverityLevel), nullable=False, default=SeverityLevel.LOW)
    status = Column(Enum(DisasterStatus), nullable=False, default=DisasterStatus.DETECTED)

    # Location info
    location_name = Column(String(255))
    latitude = Column(Float)
    longitude = Column(Float)

    # Where the data came from (BMKG, Twitter, etc)
    source = Column(String(100))
    source_url = Column(String(500))

    # Timestamps
    detected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # AI analysis from the Assessment Agent
    ai_assessment = Column(Text)

    # Relationships — one disaster can have many resource deployments
    resources = relationship("ResourceDeployment", back_populates="disaster")


class Resource(Base):
    """
    Represents an available resource (volunteer group, medical kit, etc).
    Managed by the Coordinator Agent.
    """
    __tablename__ = "resources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    resource_type = Column(Enum(ResourceType), nullable=False)
    quantity = Column(Integer, default=1)
    location_name = Column(String(255))
    latitude = Column(Float)
    longitude = Column(Float)
    is_available = Column(Integer, default=1)  # SQLite has no boolean, use 0/1

    deployments = relationship("ResourceDeployment", back_populates="resource")


class ResourceDeployment(Base):
    """
    Tracks which resources are deployed to which disaster.
    This is a join table with extra data (the 'why' and 'when').
    """
    __tablename__ = "resource_deployments"

    id = Column(Integer, primary_key=True, index=True)
    disaster_id = Column(Integer, ForeignKey("disasters.id"), nullable=False)
    resource_id = Column(Integer, ForeignKey("resources.id"), nullable=False)
    deployed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    notes = Column(Text)  # Coordinator agent's reasoning

    disaster = relationship("Disaster", back_populates="resources")
    resource = relationship("Resource", back_populates="deployments")


class AgentLog(Base):
    """
    Audit log of every action taken by any agent.
    Critical for transparency — judges can see what agents actually did.
    """
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, index=True)
    agent_name = Column(String(100))       # e.g., "MonitorAgent"
    action = Column(String(255))           # e.g., "detected_earthquake"
    details = Column(Text)                 # JSON string with full context
    disaster_id = Column(Integer, ForeignKey("disasters.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class User(Base):
    """
    System users — responders, coordinators, admins.
    Needed for JWT authentication.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default="responder")  # admin, coordinator, responder
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
