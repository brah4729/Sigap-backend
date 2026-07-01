"""
SIGAP MCP Server — exposes disaster data as MCP tools.

MCP (Model Context Protocol) is an open standard by Anthropic that
lets AI agents talk to external services using a consistent protocol.
Think of it like a "USB standard for AI tools."

By building an MCP server, ANY MCP-compatible AI (Claude, Gemini via
ADK, etc.) can query SIGAP's real-time disaster data without needing
custom integration code.

This server exposes 3 tools:
  1. get_active_disasters  — list current disaster events
  2. get_disaster_detail   — deep info on a specific disaster
  3. get_resource_status   — check what resources are available

Run standalone with:
    python mcp/disaster_mcp_server.py

The OrchestratorAgent (and any external AI) connects to this server
via stdio transport.
"""

import asyncio
import json
import sys
import os

# Add parent directory to path so we can import our own modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp_server import types as mcp_types

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from dotenv import load_dotenv

load_dotenv()

# --- Database setup (standalone, outside FastAPI) ---
# The MCP server runs as its own process, so we need our own
# database connection, separate from FastAPI's connection pool.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./sigap.db")

engine = create_async_engine(DATABASE_URL, connect_args={"check_same_thread": False})
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


# --- Create the MCP Server ---
# The server name ("sigap-disaster-mcp") is how external agents
# identify and connect to this server.
server = Server("sigap-disaster-mcp")


# --- Tool 1: get_active_disasters ---
# Returns current disaster events from the database.
# External AI agents call this to know what's happening right now.

@server.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    """
    Declare which tools this MCP server exposes.
    Think of this as the "menu" of capabilities.
    """
    return [
        mcp_types.Tool(
            name="get_active_disasters",
            description=(
                "Fetch active disaster events in Indonesia. "
                "Returns disasters detected by SIGAP monitoring agents, "
                "including location, severity, and current status. "
                "Use this to get an overview of current disaster situations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "severity_filter": {
                        "type": "string",
                        "description": "Optional: filter by severity (LOW, MEDIUM, HIGH, CRITICAL)",
                        "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of disasters to return (default: 20)",
                        "default": 20,
                    },
                },
                "required": [],
            },
        ),
        mcp_types.Tool(
            name="get_disaster_detail",
            description=(
                "Get detailed information about a specific disaster event, "
                "including the AI assessment, affected population estimate, "
                "recommended response actions, and deployed resources. "
                "Use this after get_active_disasters to drill into a specific event."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "disaster_id": {
                        "type": "integer",
                        "description": "The ID of the disaster to retrieve details for",
                    },
                },
                "required": ["disaster_id"],
            },
        ),
        mcp_types.Tool(
            name="get_resource_status",
            description=(
                "Check the status of disaster response resources across Indonesia. "
                "Returns available and deployed resources including medical teams, "
                "volunteers, vehicles, shelters, and food supplies. "
                "Use this to understand response capacity."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "available_only": {
                        "type": "boolean",
                        "description": "If true, only return available (not yet deployed) resources",
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[mcp_types.TextContent]:
    """
    Handle tool calls from external AI agents.
    
    This is the core of the MCP server — when an AI calls one of our
    tools, this function executes it and returns the result.
    
    All results are returned as TextContent (JSON strings) since MCP
    communicates via text-based protocol.
    """
    async with AsyncSessionLocal() as db:

        # --- Tool: get_active_disasters ---
        if name == "get_active_disasters":
            from db.models import Disaster, SeverityLevel

            severity_filter = arguments.get("severity_filter")
            limit = arguments.get("limit", 20)

            query = select(Disaster).order_by(Disaster.detected_at.desc()).limit(limit)

            if severity_filter:
                query = query.where(Disaster.severity == SeverityLevel(severity_filter))

            result = await db.execute(query)
            disasters = result.scalars().all()

            data = [
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
                    "detected_at": d.detected_at.isoformat() if d.detected_at else None,
                }
                for d in disasters
            ]

            return [mcp_types.TextContent(
                type="text",
                text=json.dumps({
                    "tool": "get_active_disasters",
                    "count": len(data),
                    "disasters": data,
                }, ensure_ascii=False, indent=2)
            )]

        # --- Tool: get_disaster_detail ---
        elif name == "get_disaster_detail":
            from db.models import Disaster, ResourceDeployment
            from sqlalchemy.orm import selectinload

            disaster_id = arguments.get("disaster_id")
            if not disaster_id:
                return [mcp_types.TextContent(type="text", text='{"error": "disaster_id is required"}')]

            result = await db.execute(
                select(Disaster)
                .options(selectinload(Disaster.resources).selectinload(ResourceDeployment.resource))
                .where(Disaster.id == disaster_id)
            )
            disaster = result.scalar_one_or_none()

            if not disaster:
                return [mcp_types.TextContent(type="text", text=f'{{"error": "Disaster {disaster_id} not found"}}')]

            # Parse the AI assessment JSON stored in the field
            try:
                assessment = json.loads(disaster.ai_assessment) if disaster.ai_assessment else {}
            except json.JSONDecodeError:
                assessment = {"raw": disaster.ai_assessment}

            deployments = [
                {
                    "resource_name": dep.resource.name if dep.resource else "Unknown",
                    "resource_type": dep.resource.resource_type.value if dep.resource else "Unknown",
                    "notes": dep.notes,
                    "deployed_at": dep.deployed_at.isoformat() if dep.deployed_at else None,
                }
                for dep in disaster.resources
            ]

            data = {
                "id": disaster.id,
                "title": disaster.title,
                "type": disaster.disaster_type.value,
                "severity": disaster.severity.value,
                "status": disaster.status.value,
                "location": disaster.location_name,
                "latitude": disaster.latitude,
                "longitude": disaster.longitude,
                "source": disaster.source,
                "detected_at": disaster.detected_at.isoformat() if disaster.detected_at else None,
                "ai_assessment": assessment,
                "deployed_resources": deployments,
            }

            return [mcp_types.TextContent(
                type="text",
                text=json.dumps({"tool": "get_disaster_detail", "disaster": data}, ensure_ascii=False, indent=2)
            )]

        # --- Tool: get_resource_status ---
        elif name == "get_resource_status":
            from db.models import Resource

            available_only = arguments.get("available_only", False)
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
            deployed_count = len(data) - available_count

            return [mcp_types.TextContent(
                type="text",
                text=json.dumps({
                    "tool": "get_resource_status",
                    "total_resources": len(data),
                    "available": available_count,
                    "deployed": deployed_count,
                    "resources": data,
                }, ensure_ascii=False, indent=2)
            )]

        else:
            return [mcp_types.TextContent(type="text", text=f'{{"error": "Unknown tool: {name}"}}')]


# --- Run the server ---
async def main():
    """
    Start the MCP server using stdio transport.
    
    stdio = the server communicates via standard input/output.
    This is the standard MCP transport for local servers.
    External agents connect to this process and exchange JSON messages.
    """
    print("🚨 SIGAP MCP Server starting...", file=sys.stderr)
    print("Tools available: get_active_disasters, get_disaster_detail, get_resource_status", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
