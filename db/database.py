"""
Database connection setup using SQLAlchemy async engine.

Why async? Because FastAPI is async by default — using an async
database driver means we don't block the server while waiting
for DB queries. This is critical for handling multiple requests
at the same time (e.g., many users checking disaster status).
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./sigap.db")

# Create the async engine
# check_same_thread=False is SQLite-specific — needed because
# async can access the DB from different threads.
engine = create_async_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,  # Set to True to see all SQL queries (useful for debugging)
)

# Session factory — use this to create DB sessions in route handlers
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Base class that all our models will inherit from
class Base(DeclarativeBase):
    pass


# Dependency — inject a DB session into any FastAPI route
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# Called once on startup to create all tables
async def init_db():
    from db import models  # Import here to avoid circular imports
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
