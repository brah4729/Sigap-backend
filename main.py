"""
SIGAP - Sistem Informasi Geospasial Agent Platform
Main FastAPI application entry point.

This file:
- Creates the FastAPI app instance
- Registers all API routers
- Configures CORS (so our Next.js frontend can talk to us)
- Initializes the database on startup
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from db.database import init_db
from api.routes import disasters, agents, auth, resources

# Silence ADK's internal "Node execution failed" tracebacks.
# These fire on every 503 retry attempt even though we already
# catch and handle the error ourselves in monitor_agent.py /
# assessment_agent.py. This is purely cosmetic noise reduction —
# our own [AgentName] print statements still show what's happening.
logging.getLogger("google_adk").setLevel(logging.CRITICAL)
logging.getLogger("google.adk").setLevel(logging.CRITICAL)


# --- Lifespan: runs on startup and shutdown ---
# We use this to initialize the database before
# the app starts accepting requests.
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    print("🚨 SIGAP starting up...")
    await init_db()
    print("✅ Database initialized")
    yield
    # SHUTDOWN
    print("🛑 SIGAP shutting down...")


# --- Create app ---
app = FastAPI(
    title="SIGAP API",
    description="Disaster Response Coordination Multi-Agent System",
    version="1.0.0",
    lifespan=lifespan,
)

# --- CORS Middleware ---
# Allows our Next.js frontend (running on port 3000) to call this API.
# In production, replace "*" with your actual frontend URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Register Routers ---
# Each router handles a specific domain of the API.
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(disasters.router, prefix="/api/disasters", tags=["Disasters"])
app.include_router(agents.router, prefix="/api/agents", tags=["Agents"])
app.include_router(resources.router, prefix="/api/resources", tags=["Resources"])


# --- Health Check ---
@app.get("/")
async def root():
    return {
        "status": "online",
        "app": "SIGAP",
        "version": "1.0.0",
        "message": "Disaster Response Multi-Agent System is running 🚨",
    }
