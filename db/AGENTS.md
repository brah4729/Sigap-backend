# Database Folder — AGENTS.md

Everything about the database layer lives here.

---

## What's In Here

```
db/
├── database.py   ← Async engine, session factory, get_db() dependency
├── models.py     ← All SQLAlchemy table definitions
└── __init__.py
```

---

## Database Choice: SQLite + aiosqlite

We use SQLite because:
- Zero setup — file-based, no server needed
- Perfect for hackathon / local demo
- `aiosqlite` makes it async so it doesn't block FastAPI

The database file is `Backend/sigap.db`.
It's auto-created on startup by `init_db()` in `main.py`.

**For production:** swap to PostgreSQL with asyncpg driver.
Only change needed is `DATABASE_URL` in `.env`.

---

## Tables Overview

| Table | Purpose | Key Fields |
|---|---|---|
| `disasters` | Disaster events detected by MonitorAgent | severity, status, lat/lon, ai_assessment |
| `resources` | Available response resources | type, quantity, location, is_available |
| `resource_deployments` | Which resource went to which disaster | disaster_id, resource_id, notes |
| `agent_logs` | Audit trail of every agent action | agent_name, action, details, disaster_id |
| `users` | Auth users (responders, admins) | username, hashed_password, role |

---

## Important: SQLite has no Boolean type

SQLite stores booleans as integers. We use `0` and `1`.

```python
# In models.py
is_available = Column(Integer, default=1)  # 1=True, 0=False

# When reading
if resource.is_available == 1:  # not `if resource.is_available:`
```

---

## Important: Async Session Rules

Always use `get_db()` as a FastAPI dependency.
Never create sessions manually in route handlers.

```python
# ✅ Correct
@router.get("/")
async def route(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Disaster))

# ❌ Wrong — session leaks
async def route():
    db = AsyncSessionLocal()
    result = await db.execute(select(Disaster))
```

## flush() vs commit()

```python
db.add(disaster)
await db.flush()    # sends SQL, gets auto-generated ID, still reversible
disaster_id = disaster.id  # now available!
await db.commit()   # finalizes everything permanently
```

Use `flush()` when you need the ID of a newly inserted row
before the transaction is committed.

---

## Adding a New Table

1. Add model class to `db/models.py` inheriting from `Base`
2. Delete `sigap.db` (or run a migration)
3. Restart uvicorn — `init_db()` recreates all tables automatically

No migration tool needed for SQLite in development.
In production with PostgreSQL, use Alembic for migrations.
