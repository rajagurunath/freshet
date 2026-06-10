"""Async worker that drains the job queue.

The Worker is started as a background asyncio Task in the FastAPI lifespan.
It loops indefinitely:
  1. Claim the next queued job.
  2. Run the handler in a thread (asyncio.to_thread) so CPU-bound or
     blocking I/O does not stall the event loop.
  3. Mark the job done or error.
  4. Sleep briefly when no jobs are available to avoid busy-polling.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Callable, Optional

from contexthub.jobs.store import JobStore

logger = logging.getLogger(__name__)

# Type alias: a handler is a synchronous callable that receives a payload dict
# and returns a result dict (or raises).
HandlerFn = Callable[[dict[str, Any]], dict[str, Any]]


class Worker:
    """Single-threaded async worker that processes jobs from JobStore."""

    def __init__(
        self,
        store: JobStore,
        handlers: dict[str, HandlerFn],
        poll_interval: float = 1.0,
    ) -> None:
        self._store = store
        self._handlers = handlers
        self._poll_interval = poll_interval
        self._running = False

    def register(self, kind: str, handler: HandlerFn) -> None:
        """Register a handler for a job kind (can be called after construction)."""
        self._handlers[kind] = handler

    async def run(self) -> None:
        """Main loop — runs until cancelled."""
        self._running = True
        logger.info("Job worker started (poll_interval=%.2fs)", self._poll_interval)
        try:
            while True:
                job = self._store.claim_next()
                if job is None:
                    await asyncio.sleep(self._poll_interval)
                    continue

                job_id = job["id"]
                kind = job["kind"]
                payload = job.get("payload") or {}

                handler = self._handlers.get(kind)
                if handler is None:
                    err = f"No handler registered for job kind '{kind}'"
                    logger.error("Job %s: %s", job_id, err)
                    self._store.fail(job_id, error=err)
                    continue

                logger.info("Processing job %s (kind=%s)", job_id, kind)
                try:
                    result = await asyncio.to_thread(handler, payload)
                    self._store.complete(job_id, result=result or {})
                    logger.info("Job %s completed successfully", job_id)
                except Exception as exc:
                    err_msg = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                    logger.error("Job %s failed: %s", job_id, err_msg)
                    self._store.fail(job_id, error=str(exc))
        except asyncio.CancelledError:
            logger.info("Job worker stopped")
            self._running = False
            raise
