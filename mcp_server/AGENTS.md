# MCP Server Folder — AGENTS.md

Our custom MCP (Model Context Protocol) server lives here.

---

## IMPORTANT: Why is this folder called mcp_server/ not mcp/?

The `mcp` Python package (installed via pip) conflicts with any local
folder also named `mcp/`. Python finds local folders FIRST, so if
you name your folder `mcp/`, Python imports your empty `__init__.py`
instead of the real library, causing:

```
ImportError: cannot import name 'SamplingCapability' from 'mcp'
```

**Rule: Never name a folder the same as a pip package you use.**
Common dangerous names: `mcp`, `json`, `os`, `typing`, `fastapi`

---

## What is MCP?

MCP (Model Context Protocol) is an open standard created by Anthropic.
It's like a "USB standard for AI tools" — any MCP-compatible AI
(Claude, Gemini via ADK, etc.) can connect to any MCP server
and use its tools without custom integration code.

Think of it like this:
```
Without MCP:
  Every AI needs custom code to talk to your database.

With MCP:
  One server speaks the standard protocol.
  Any AI just connects and uses the tools.
```

---

## What's In Here

```
mcp_server/
├── disaster_mcp_server.py  ← The actual MCP server
└── AGENTS.md               ← this file
```

---

## Tools Exposed by Our MCP Server

### get_active_disasters
Fetches current disaster events from the SIGAP database.
- Optional: filter by severity (LOW/MEDIUM/HIGH/CRITICAL)
- Optional: limit number of results
- Returns: list of disasters with location, severity, status

### get_disaster_detail
Deep info on one specific disaster.
- Required: disaster_id
- Returns: full assessment, deployed resources, AI analysis

### get_resource_status
Check all response resources across Indonesia.
- Optional: available_only=true to filter
- Returns: resources with availability status

---

## Two Ways to Use These Tools

### 1. Via MCP Protocol (external clients — e.g. Claude Desktop)
Run the server as a standalone process:
```bash
python mcp_server/disaster_mcp_server.py
```
Any MCP client can then connect and call the tools.

### 2. Direct Python calls (internal — OrchestratorAgent)
The same tool logic is imported directly in `orchestrator_agent.py`
as Python functions (`mcp_get_active_disasters`, `mcp_get_resource_status`).

Why? ADK 2.x removed `MCPToolset.from_server()` that existed in 1.x.
Direct import is more reliable and avoids subprocess management.
The MCP server still exists for external clients — internally we
just skip the protocol overhead.

---

## How MCP Communication Works (stdio transport)

The server communicates via standard input/output (stdin/stdout).
External clients send JSON messages in, get JSON responses out.
The `mcp` library handles all the protocol framing for us.
