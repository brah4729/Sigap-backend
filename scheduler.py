"""
Background scheduler for SIGAP.

Runs periodic tasks while FastAPI is alive:
  - Every 2 hours: clean old disasters + scan fresh data
  - Every 10 minutes: trigger monitor scan (keeps data fresh)

Why asyncio instead of a library like Celery?
  For a hackathon/small project, asyncio background tasks are perfect.
  Celery requires Redis/RabbitMQ as a message broker — overkill here.
  asyncio.create_task() runs alongside FastAPI with zero extra setup.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import Disaster, AgentLog, ResourceDeployment, Resource


async def cleanup_old_data(older_than_hours: int = 3):
    """
    Delete disasters and related data older than `older_than_hours`.

    Why cascade manually?
      SQLite doesn't enforce FK cascades by default, so we manually
      delete child records (deployments, logs) before parent (disasters).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)

    async with AsyncSessionLocal() as db:
        try:
            # Step 1: Find old disaster IDs
            from sqlalchemy import select
            result = await db.execute(
                select(Disaster.id).where(Disaster.detected_at < cutoff)
            )
            old_ids = [row[0] for row in result.fetchall()]

            if not old_ids:
                print(f"[Scheduler] No disasters older than {older_than_hours}h to clean")
                return 0

            # Step 2: Delete child records first (FK constraint)
            await db.execute(
                delete(ResourceDeployment).where(
                    ResourceDeployment.disaster_id.in_(old_ids)
                )
            )
            await db.execute(
                delete(AgentLog).where(
                    AgentLog.disaster_id.in_(old_ids)
                )
            )

            # Step 3: Delete the disasters themselves
            await db.execute(
                delete(Disaster).where(Disaster.id.in_(old_ids))
            )

            # Step 4: Reset resources to available
            # (deployed resources get freed up when their disaster is cleaned)
            await db.execute(
                Resource.__table__.update().values(is_available=1)
            )

            await db.commit()
            print(f"[Scheduler] ✅ Cleaned {len(old_ids)} old disasters (>{older_than_hours}h)")
            return len(old_ids)

        except Exception as e:
            await db.rollback()
            print(f"[Scheduler] ❌ Cleanup failed: {e}")
            return 0


async def run_fresh_scan():
    """Trigger the Monitor Agent to fetch fresh disaster data."""
    async with AsyncSessionLocal() as db:
        try:
            from agents.monitor_agent import run_monitor_agent
            result = await run_monitor_agent(db)
            print(f"[Scheduler] 🔍 Auto-scan: {result['message']}")
        except Exception as e:
            print(f"[Scheduler] ❌ Auto-scan failed: {e}")


async def cleanup_loop(interval_hours: int = 2):
    """
    Main cleanup loop — runs forever in the background.

    Flow every `interval_hours`:
      1. Clean disasters older than 3 hours
      2. Immediately run a fresh scan to repopulate
      3. Sleep until next cycle
    """
    print(f"[Scheduler] 🕐 Cleanup loop started — runs every {interval_hours}h")

    # Wait a bit before the first run so startup finishes cleanly
    await asyncio.sleep(60)  # 1 minute after startup

    while True:
        print(f"[Scheduler] Running scheduled cleanup...")

        # Clean old data
        cleaned = await cleanup_old_data(older_than_hours=3)

        # If we cleaned something, do a fresh scan to repopulate
        if cleaned > 0:
            print(f"[Scheduler] Running fresh scan after cleanup...")
            await asyncio.sleep(3)  # small pause
            await run_fresh_scan()

        # Sleep until next cycle
        sleep_seconds = interval_hours * 3600
        next_run = datetime.now(timezone.utc) + timedelta(seconds=sleep_seconds)
        print(f"[Scheduler] 💤 Next cleanup at {next_run.strftime('%H:%M UTC')}")
        await asyncio.sleep(sleep_seconds)


async def monitor_loop(interval_minutes: int = 10):
    """
    Lightweight monitor loop — scans for new disasters every N minutes.
    Keeps the dashboard fresh without waiting for the cleanup cycle.
    """
    print(f"[Scheduler] 📡 Monitor loop started — scans every {interval_minutes}m")

    # Wait for startup + cleanup loop's first check
    await asyncio.sleep(120)  # 2 minutes after startup

    while True:
        await asyncio.sleep(interval_minutes * 60)
        print(f"[Scheduler] 📡 Running scheduled monitor scan...")
        await run_fresh_scan()
