"""Subscription-window harvester (Task 12).

Rationale
---------
Many developers pay for a weekly Claude / Codex subscription that resets at a
fixed time (e.g. every Monday at 00:00 UTC).  Unused quota simply evaporates.
This harvester detects when we are approaching the reset and burns the remaining
quota on useful background work — summarizing sessions that lack summaries and
kicking off knowledge-graph extraction — so none of the quota goes to waste.

IMPORTANT: This is inherently *time-based best-effort*.  The CLI providers do not
expose a remaining-quota API, so we cannot know the actual balance.  We simply
start draining pending work inside the lookahead window and stop if the provider
starts erroring (which we interpret as a rate-limit / quota signal).

Config (see contexthub.config.Settings)
-----------------------------------------
  HARVEST_ENABLED          bool        default False
  HARVEST_PROVIDERS        str (csv)   default "claude-cli,codex-cli"
  HARVEST_WINDOW_RESET     str         default "mon 00:00"
  HARVEST_LOOKAHEAD_HOURS  int         default 12

Handler: harvest_check_handler
-------------------------------
Registered as kind="harvest_check" in the job registry.

Payload keys (all optional — handler reads settings as fallback):
  harvest_enabled      bool   — override settings.harvest_enabled
  window_reset         str    — override settings.harvest_window_reset
  lookahead_hours      int    — override settings.harvest_lookahead_hours
  job_store            Any    — injected by tests / worker
  _now_override        str    — ISO datetime; test hook to fake the clock

Returns a result dict:
  action               str    — "skipped" | "drained"
  reason               str?   — why skipped ("disabled" | "outside_lookahead")
  next_reset           str    — ISO datetime of the next window reset
  pending_summarize    int?   — number of sessions lacking summaries (drained)
  pending_graph        int?   — number of sessions lacking graph extraction (drained)
  error                str?   — first error message if a sub-job enqueue failed

Public helpers (used by the /v1/harvest/status endpoint)
---------------------------------------------------------
  parse_window_reset(spec)   → (iso_weekday: int, hour: int, minute: int)
  next_reset_datetime(...)   → datetime (UTC)
  within_lookahead(...)      → bool
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Weekday name → ISO weekday number (Monday=1 … Sunday=7)
_WEEKDAY_MAP = {
    "mon": 1, "tue": 2, "wed": 3, "thu": 4,
    "fri": 5, "sat": 6, "sun": 7,
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def parse_window_reset(spec: str) -> tuple[int, int, int]:
    """Parse a cron-ish window-reset spec into (iso_weekday, hour, minute).

    Format: ``"<weekday> <HH:MM>"``  e.g. ``"mon 00:00"`` or ``"fri 12:30"``.

    Returns
    -------
    (iso_weekday, hour, minute)  where iso_weekday follows Python's isoweekday()
    convention: Monday=1, …, Sunday=7.

    Raises
    ------
    ValueError  on unrecognised format.
    """
    parts = spec.strip().lower().split()
    if len(parts) != 2:
        raise ValueError(f"Invalid window_reset spec {spec!r}; expected '<weekday> <HH:MM>'")

    weekday_str, time_str = parts
    iso_weekday = _WEEKDAY_MAP.get(weekday_str)
    if iso_weekday is None:
        raise ValueError(
            f"Unrecognised weekday {weekday_str!r} in spec {spec!r}; "
            f"accepted: {list(_WEEKDAY_MAP)}"
        )

    time_parts = time_str.split(":")
    if len(time_parts) != 2:
        raise ValueError(f"Invalid time {time_str!r} in spec {spec!r}; expected 'HH:MM'")
    hour = int(time_parts[0])
    minute = int(time_parts[1])
    return iso_weekday, hour, minute


def next_reset_datetime(
    iso_weekday: int,
    hour: int,
    minute: int,
    *,
    ref: Optional[datetime] = None,
) -> datetime:
    """Return the next UTC datetime when the subscription window resets.

    Parameters
    ----------
    iso_weekday : int
        Monday=1 … Sunday=7
    hour, minute : int
        UTC time of the reset.
    ref : datetime, optional
        Reference "now".  Defaults to ``datetime.now(timezone.utc)``.

    Returns
    -------
    A timezone-aware UTC datetime strictly in the future relative to *ref*.
    """
    if ref is None:
        ref = datetime.now(timezone.utc)

    # Candidate: this week's reset day at the given time
    days_ahead = iso_weekday - ref.isoweekday()
    candidate = (ref + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0,
    )
    # If the candidate is not strictly in the future, push it 7 days forward.
    if candidate <= ref:
        candidate += timedelta(weeks=1)
    return candidate


def within_lookahead(
    now: datetime,
    reset: datetime,
    lookahead_hours: int,
) -> bool:
    """Return True iff *now* is within *lookahead_hours* **before** *reset*.

    Both *now* and *reset* must be timezone-aware.
    Returns False if *now* is past the reset.
    """
    delta = reset - now
    if delta.total_seconds() <= 0:
        return False  # already past the reset
    return delta.total_seconds() <= lookahead_hours * 3600


# ---------------------------------------------------------------------------
# Lazy accessor for vector store (allows tests to patch it cleanly)
# ---------------------------------------------------------------------------

def get_vector_store_fn():
    """Return the application's VectorStore singleton.

    Defined as a module-level function so tests can patch
    ``contexthub.jobs.harvest.get_vector_store_fn`` without touching the
    actual singleton or import machinery.
    """
    from contexthub.storage.vectors import get_vector_store
    return get_vector_store()


# ---------------------------------------------------------------------------
# harvest_check handler
# ---------------------------------------------------------------------------

def harvest_check_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Drain pending summarization / graph work before the subscription window resets.

    See module docstring for full description.

    Payload keys (all optional):
      harvest_enabled    bool  — whether harvesting is active
      window_reset       str   — cron-ish reset spec (e.g. "mon 00:00")
      lookahead_hours    int   — hours before reset to start draining
      job_store          Any   — JobStore instance (injected by worker / tests)
      _now_override      str   — ISO datetime for clock override in tests
    """
    # --- resolve settings -----------------------------------------------
    harvest_enabled: bool = payload.get("harvest_enabled", True)  # test can override
    if not harvest_enabled:
        # Re-read from actual settings when not explicitly overridden in payload
        try:
            from contexthub.config import get_settings
            settings = get_settings()
            harvest_enabled = settings.harvest_enabled
        except Exception:
            harvest_enabled = False

    # Honour explicit False from payload (test hook)
    if "harvest_enabled" in payload and payload["harvest_enabled"] is False:
        harvest_enabled = False

    if not harvest_enabled:
        logger.info("harvest_check_handler: harvest disabled — skipping")
        return {"action": "skipped", "reason": "disabled"}

    # --- clock / config --------------------------------------------------
    now_override = payload.get("_now_override")
    if now_override:
        now = datetime.fromisoformat(now_override)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    window_reset_spec: str = payload.get("window_reset") or "mon 00:00"
    lookahead_hours: int = int(payload.get("lookahead_hours") or 12)

    # Try to pull from Settings if not in payload
    if "window_reset" not in payload or "lookahead_hours" not in payload:
        try:
            from contexthub.config import get_settings
            settings = get_settings()
            if "window_reset" not in payload:
                window_reset_spec = settings.harvest_window_reset
            if "lookahead_hours" not in payload:
                lookahead_hours = settings.harvest_lookahead_hours
        except Exception:
            pass

    iso_weekday, hour, minute = parse_window_reset(window_reset_spec)
    reset = next_reset_datetime(iso_weekday, hour, minute, ref=now)
    reset_iso = reset.isoformat()

    # --- lookahead check -------------------------------------------------
    if not within_lookahead(now, reset, lookahead_hours):
        logger.info(
            "harvest_check_handler: outside lookahead window "
            "(reset=%s, lookahead=%dh, now=%s) — skipping",
            reset_iso,
            lookahead_hours,
            now.isoformat(),
        )
        # Still schedule the next hourly check
        _reschedule_next(payload.get("job_store"), now, window_reset_spec, lookahead_hours)
        return {
            "action": "skipped",
            "reason": "outside_lookahead",
            "next_reset": reset_iso,
        }

    # --- drain logic -----------------------------------------------------
    logger.info(
        "harvest_check_handler: within lookahead of %s — draining pending work",
        reset_iso,
    )
    job_store = payload.get("job_store") or _get_job_store()
    vectors = get_vector_store_fn()

    try:
        result = vectors.list_sessions(limit=500, offset=0, sort="created_at", order="desc")
    except Exception as exc:
        logger.error("harvest_check_handler: failed to list sessions: %s", exc)
        return {"action": "drained", "error": str(exc), "next_reset": reset_iso}

    items = result.get("items", [])

    pending_summarize: list[str] = []
    pending_graph: list[str] = []

    for row in items:
        sid = row.get("id", "")
        if not (row.get("summary") or "").strip():
            pending_summarize.append(sid)
        # graph_extracted is False/missing → needs graph extraction
        if not row.get("graph_extracted", False):
            pending_graph.append(sid)

    first_error: Optional[str] = None

    # Enqueue summarize_batch for summary-less sessions
    if pending_summarize:
        try:
            job_store.enqueue(
                kind="summarize_batch",
                payload={
                    "session_ids": pending_summarize,
                    "provider": "default",
                },
            )
            logger.info(
                "harvest_check_handler: enqueued summarize_batch for %d sessions",
                len(pending_summarize),
            )
        except Exception as exc:
            first_error = str(exc)
            logger.error(
                "harvest_check_handler: failed to enqueue summarize_batch: %s", exc
            )

    # Enqueue graph_extract for sessions lacking graph extraction
    # (only if no error so far — stop on provider error)
    if pending_graph and first_error is None:
        for sid in pending_graph:
            try:
                job_store.enqueue(
                    kind="graph_extract",
                    payload={"session_id": sid},
                )
            except Exception as exc:
                first_error = str(exc)
                logger.error(
                    "harvest_check_handler: failed to enqueue graph_extract for %s: %s",
                    sid,
                    exc,
                )
                break  # stop on first error

    # Schedule the next hourly harvest_check
    _reschedule_next(job_store, now, window_reset_spec, lookahead_hours)

    return_val: dict[str, Any] = {
        "action": "drained",
        "next_reset": reset_iso,
        "pending_summarize": len(pending_summarize),
        "pending_graph": len(pending_graph),
    }
    if first_error:
        return_val["error"] = first_error

    return return_val


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_job_store():
    """Return a JobStore pointed at the configured jobs DB."""
    from contexthub.config import get_settings
    from contexthub.jobs.store import JobStore

    settings = get_settings()
    return JobStore(settings.jobs_db)


def _reschedule_next(
    job_store,
    now: datetime,
    window_reset_spec: str,
    lookahead_hours: int,
) -> None:
    """Enqueue the next harvest_check job, scheduled 1 hour from now."""
    if job_store is None:
        return
    next_run = (now + timedelta(hours=1)).isoformat()
    try:
        job_store.enqueue(
            kind="harvest_check",
            payload={
                "window_reset": window_reset_spec,
                "lookahead_hours": lookahead_hours,
            },
            scheduled_for=next_run,
        )
        logger.debug("harvest_check_handler: next run scheduled for %s", next_run)
    except Exception as exc:
        logger.warning("harvest_check_handler: could not schedule next run: %s", exc)


# ---------------------------------------------------------------------------
# Harvest status helper (used by GET /v1/harvest/status)
# ---------------------------------------------------------------------------

def get_harvest_status(settings) -> dict[str, Any]:
    """Return a status dict for the /v1/harvest/status endpoint.

    Keys:
      harvest_enabled    bool
      next_reset         str  (ISO-8601 UTC)
      pending_counts     {pending_summarize: int, pending_graph_extract: int}
      last_drain_results dict | None
    """
    harvest_enabled = settings.harvest_enabled

    try:
        iso_weekday, hour, minute = parse_window_reset(settings.harvest_window_reset)
        reset = next_reset_datetime(iso_weekday, hour, minute)
        next_reset_iso = reset.isoformat()
    except Exception as exc:
        logger.warning("get_harvest_status: could not compute next_reset: %s", exc)
        next_reset_iso = ""

    # Count pending work
    pending_summarize = 0
    pending_graph = 0
    try:
        vectors = get_vector_store_fn()
        result = vectors.list_sessions(limit=500, offset=0, sort="created_at", order="desc")
        for row in result.get("items", []):
            if not (row.get("summary") or "").strip():
                pending_summarize += 1
            if not row.get("graph_extracted", False):
                pending_graph += 1
    except Exception as exc:
        logger.warning("get_harvest_status: could not count pending work: %s", exc)

    # Most recent drain result
    last_drain: Optional[dict] = None
    try:
        job_store = _get_job_store()
        done_jobs = job_store.list(kind="harvest_check", status="done")
        if done_jobs:
            # Most recent first
            last_done = done_jobs[0]
            last_drain = last_done.get("result") or {}
    except Exception as exc:
        logger.warning("get_harvest_status: could not fetch last drain results: %s", exc)

    return {
        "harvest_enabled": harvest_enabled,
        "next_reset": next_reset_iso,
        "pending_counts": {
            "pending_summarize": pending_summarize,
            "pending_graph_extract": pending_graph,
        },
        "last_drain_results": last_drain,
    }
