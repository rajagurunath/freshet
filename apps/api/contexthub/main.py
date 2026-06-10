"""Context Hub API — application entry point.

Run locally:
    python -m contexthub.main
    # or after pip install -e .
    contexthub-api

The server listens on port 8787 by default.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contexthub.api.routes import router
from contexthub.config import get_settings

logger = logging.getLogger(__name__)


def _next_02_utc() -> str:
    """Return the ISO-8601 timestamp for the next 02:00 UTC (today or tomorrow)."""
    now = datetime.now(timezone.utc)
    candidate = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _ensure_harvest_check(job_store, harvest_enabled: bool = False) -> None:  # type: ignore[type-arg]
    """Enqueue an initial harvest_check job if none is already queued or running.

    The job is scheduled immediately (no delay).  The handler will reschedule
    itself hourly so there is always exactly one future harvest_check in the queue.

    This is a no-op when harvest_enabled is False.
    """
    if not harvest_enabled:
        return
    existing = job_store.list(kind="harvest_check", status="queued")
    running = job_store.list(kind="harvest_check", status="running")
    if existing or running:
        logger.info(
            "harvest_check job already queued/running (%d queued, %d running)",
            len(existing),
            len(running),
        )
        return
    jid = job_store.enqueue(kind="harvest_check", payload={})
    logger.info("Enqueued initial harvest_check job %s", jid)


def _ensure_nightly_summarize_pending(job_store) -> None:  # type: ignore[type-arg]
    """Enqueue a nightly summarize_pending job if none is already queued or running."""
    existing = job_store.list(kind="summarize_pending", status="queued")
    running = job_store.list(kind="summarize_pending", status="running")
    if existing or running:
        logger.info(
            "Nightly summarize_pending job already queued/running (%d queued, %d running)",
            len(existing),
            len(running),
        )
        return
    scheduled_for = _next_02_utc()
    jid = job_store.enqueue(
        kind="summarize_pending",
        payload={"provider": "default"},
        scheduled_for=scheduled_for,
    )
    logger.info("Scheduled nightly summarize_pending job %s for %s", jid, scheduled_for)

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info("Context Hub API starting up")
        logger.info("  embedding_provider : %s", settings.embedding_provider)
        logger.info("  lancedb_uri        : %s", settings.lancedb_uri)
        logger.info("  blob_dir           : %s", settings.blob_dir)
        logger.info("  jobs_db            : %s", settings.jobs_db)
        logger.info("  s3_bucket          : %s", settings.s3_bucket or "(local fallback)")
        logger.info("  anthropic_model    : %s", settings.anthropic_model)
        logger.info(
            "  anthropic_api_key  : %s",
            "set" if settings.anthropic_api_key else "NOT SET (stub mode)",
        )

        # Start the background job worker
        from contexthub.jobs.handlers import HANDLER_REGISTRY
        from contexthub.jobs.store import JobStore
        from contexthub.jobs.worker import Worker

        job_store = JobStore(settings.jobs_db)
        worker = Worker(store=job_store, handlers=dict(HANDLER_REGISTRY), poll_interval=2.0)
        # Store on app state so routes can access the job store
        app.state.job_store = job_store
        worker_task = asyncio.create_task(worker.run())

        # Enqueue a nightly summarize_pending job if none is already pending.
        # Scheduled for the next 02:00 UTC so that summary-less sessions are
        # batched cheaply during off-hours (burns subscription quota right before
        # the weekly reset).
        _ensure_nightly_summarize_pending(job_store)

        # Enqueue the initial harvest_check job when the harvester is enabled.
        # The handler re-schedules itself hourly, so this only fires on cold start.
        _ensure_harvest_check(job_store, harvest_enabled=settings.harvest_enabled)

        yield

        # Shut down the worker gracefully
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        logger.info("Context Hub API shutting down")

    app = FastAPI(
        title="Context Hub API",
        description=(
            "Central API for ingesting, searching, and querying "
            "AI coding-assistant sessions across the organisation."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — allow the desktop Tauri app and local dev servers
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(router)

    return app


app = create_app()


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("contexthub.main:app", host="0.0.0.0", port=8787, reload=True)


if __name__ == "__main__":
    main()
