"""
SIGAP - Sistem Informasi Geospasial Agent Platform
Main FastAPI application entry point.

This file:
- Creates the FastAPI app instance
- Registers all API routers
- Configures CORS (so our Next.js frontend can talk to us)
- Initializes the database on startup
"""

import asyncio
import os
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

    # Start background schedulers as async tasks.
    # asyncio.create_task() runs them concurrently alongside FastAPI
    # without blocking the main server loop.
    from scheduler import cleanup_loop, monitor_loop
    cleanup_task = asyncio.create_task(cleanup_loop(interval_hours=2))
    monitor_task = asyncio.create_task(monitor_loop(interval_minutes=10))
    print("✅ Background schedulers started (cleanup: 2h, monitor: 10m)")

    yield

    # SHUTDOWN — cancel background tasks cleanly
    cleanup_task.cancel()
    monitor_task.cancel()
    print("🛑 SIGAP shutting down...")


# --- Create app ---
app = FastAPI(
    title="SIGAP API",
    description="Disaster Response Coordination Multi-Agent System",
    version="1.0.0",
    lifespan=lifespan,
)

# --- CORS Middleware ---
# Allows our Next.js frontend to call this API.
# In production, FRONTEND_URL env var should be your Vercel URL.
# e.g. FRONTEND_URL=https://sigap.vercel.app
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        FRONTEND_URL,
    ],
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
