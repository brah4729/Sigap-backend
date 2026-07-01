# Backend AGENTS.md

This is the guide for everything inside the `Backend/` folder.
Read this before modifying any backend code.

---

## Folder Structure

```
Backend/
├── main.py              ← FastAPI app entry point, CORS, routers, scheduler startup
├── config.py            ← API key management, model config — edit this for key changes
├── scheduler.py         ← Background tasks (auto-cleanup every 2h, auto-scan every 10m)
├── requirements.txt     ← Python dependencies
├── .env                 ← Secret keys (never commit this)
├── .env.example         ← Template for .env (safe to commit)
├── sigap.db             ← SQLite database (auto-created on startup)
│
├── agents/              ← The 4 AI agents (see agents/AGENTS.md)
├── api/routes/          ← FastAPI route handlers (see api/AGENTS.md)
├── db/                  ← Database models + connection (see db/AGENTS.md)
├── tools/               ← Helper functions agents call (see tools/AGENTS.md)
└── mcp_server/          ← MCP server (see mcp_server/AGENTS.md)
```

---

## Key Rules

### Always use `config.py` for API keys and model name

```python
# ✅ Correct — reads from config.py
from config import use_api_key_for, GEMINI_MODEL
use_api_key_for("monitor")
MODEL = GEMINI_MODEL

# ❌ Wrong — hardcoded, breaks when we rotate keys
MODEL = "gemini-2.5-flash-lite"
key = os.getenv("GOOGLE_API_KEY")
```

### Never hardcode model names anywhere

The model name lives ONLY in `config.py` → `GEMINI_MODEL`.
When Gemini releases new models, we update ONE place.

### Database sessions

Always use `get_db()` dependency in routes.
Never create a session manually inside a route.

```python
# ✅ Correct
@router.get("/")
async def my_route(db: AsyncSession = Depends(get_db)):
    ...

# ❌ Wrong — session not properly closed
async def my_route():
    db = AsyncSessionLocal()
    ...
```

### Committing to the database

Always `await db.flush()` before `await db.commit()` when you need
an ID after insert. `flush()` sends SQL to DB but doesn't finalize —
it lets you read back the auto-generated ID while still in the transaction.

---

## How to Add a New Route

1. Create file in `api/routes/your_thing.py`
2. Add `router = APIRouter()`
3. Register in `main.py`:
   ```python
   from api.routes import your_thing
   app.include_router(your_thing.router, prefix="/api/your_thing", tags=["YourThing"])
   ```

## How to Add a New Agent

1. Create `agents/your_agent.py`
2. Follow the pattern in `agents/monitor_agent.py`:
   - Call `use_api_key_for("your_agent")` at the start
   - Use `_run_with_retry()` for all ADK runner calls
   - Log every action with `_log_action()`
3. Add the agent key to `config.py` → `_KEY_MAP`
4. Add the env var to `.env` and `.env.example`
5. Wire it in `api/routes/agents.py` under `run_agent()`
