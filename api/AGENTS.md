# API Routes Folder — AGENTS.md

FastAPI route handlers live here. Each file = one domain.

---

## What's In Here

```
api/routes/
├── disasters.py   ← CRUD for disasters + scan trigger
├── agents.py      ← Agent control + logs + key status
├── resources.py   ← Resource management + deployments + seed
└── auth.py        ← JWT login/register/me
```

---

## Route Map

| Method | Path | What it does |
|---|---|---|
| GET | /api/disasters/ | List all disasters (optional ?status= filter) |
| GET | /api/disasters/{id} | Get one disaster |
| POST | /api/disasters/ | Manually create a disaster |
| POST | /api/disasters/scan | Trigger MonitorAgent scan |
| GET | /api/agents/logs | Agent audit log |
| GET | /api/agents/key-status | Check which API keys are configured |
| POST | /api/agents/cleanup | Manual data cleanup |
| POST | /api/agents/run/{name} | Run a specific agent |
| GET | /api/resources/ | List all resources |
| GET | /api/resources/deployments | List all deployments |
| POST | /api/resources/seed | Seed sample resources (run once) |
| POST | /api/auth/register | Create a user |
| POST | /api/auth/login | Get JWT token |
| GET | /api/auth/me | Current user info |

---

## Agent Names for /api/agents/run/{name}

Valid values: `monitor`, `assessment`, `coordinator`, `orchestrator`

Optional query param: `?disaster_id=5` (assessment + coordinator only)
Forces that specific disaster to be processed instead of all pending.

---

## Key Rules for Writing Routes

### Always import agents inside the function, not at the top

```python
# ✅ Import inside the route function
@router.post("/scan")
async def trigger_scan(db: AsyncSession = Depends(get_db)):
    from agents.monitor_agent import run_monitor_agent  # ← here
    result = await run_monitor_agent(db)
    return result

# ❌ Don't import at module top level
from agents.monitor_agent import run_monitor_agent  # circular import risk
```

Why? Agent files import db models, which import from database.py.
Top-level imports can create circular dependencies at startup.

### Always use Depends(get_db) for database access

```python
@router.get("/")
async def my_route(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MyModel))
    return result.scalars().all()
```

### Use Pydantic models for request/response validation

```python
class DisasterCreate(BaseModel):
    title: str
    severity: SeverityLevel  # uses our enum — auto-validates

class DisasterResponse(BaseModel):
    id: int
    title: str
    class Config:
        from_attributes = True  # allows converting SQLAlchemy models
```
