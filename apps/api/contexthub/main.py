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
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from contexthub.api.routes import router
from contexthub.config import get_settings

logger = logging.getLogger(__name__)

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
