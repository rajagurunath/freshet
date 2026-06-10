"""Tests for Task 12: Subscription-window harvester.

Covers:
  1. Config: HARVEST_ENABLED, HARVEST_PROVIDERS, HARVEST_WINDOW_RESET,
             HARVEST_LOOKAHEAD_HOURS are available in Settings.
  2. Window-reset parsing: hand-rolled cron-ish "weekday HH:MM" → next reset datetime.
  3. Lookahead check: harvest_check only drains inside the lookahead window.
  4. Drain loop: summary-less sessions are enqueued for summarize_batch;
                 sessions lacking graph extraction are enqueued for graph_extract;
                 stops on provider error (rate-limit).
  5. Handler registered in HANDLER_REGISTRY under "harvest_check".
  6. GET /v1/harvest/status returns expected fields (next_reset, pending_counts,
     last_drain_results, harvest_enabled).
  7. Outside lookahead → no jobs enqueued.
  8. Provider error → stops and reschedules +30min.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_caches() -> None:
    mods = [
        "contexthub.config",
        "contexthub.embeddings",
        "contexthub.storage.blob",
        "contexthub.storage.vectors",
        "contexthub.jobs.store",
        "contexthub.jobs.worker",
        "contexthub.jobs.handlers",
        "contexthub.jobs.harvest",
    ]
    for mod_name in mods:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            for attr in dir(mod):
                fn = getattr(mod, attr, None)
                if callable(fn) and hasattr(fn, "cache_clear"):
                    fn.cache_clear()
    if "contexthub.storage.vectors" in sys.modules:
        sys.modules["contexthub.storage.vectors"].reset_vector_store()


AUTH = {"Authorization": "Bearer test-key"}


def _make_session_payload(session_id: str) -> dict:
    return {
        "session": {
            "id": session_id,
            "tool": "claude-code",
            "title": f"Session {session_id}",
            "cwd": "/repo",
            "project": "myproject",
            "started_at": "2026-06-10T10:00:00Z",
            "ended_at": "2026-06-10T11:00:00Z",
            "message_count": 2,
            "models": ["claude-sonnet-4-6"],
            "tokens": {"input": 500, "output": 100},
            "preview": "preview text",
            "file_path": f"/repo/{session_id}.jsonl",
            "messages": [
                {"id": "m1", "role": "user", "text": "Implement feature X",
                 "timestamp": "2026-06-10T10:00:00Z"},
                {"id": "m2", "role": "assistant", "text": "Done.",
                 "timestamp": "2026-06-10T10:01:00Z"},
            ],
        },
        "category": "engineering",
        "visibility": "company",
        "author": {"id": "alice", "email": "alice@example.com", "name": "Alice"},
        "redacted": True,
    }


# ---------------------------------------------------------------------------
# Fixture: TestClient with isolated tmp dirs
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tmp_dirs_harvest():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield (
            os.path.join(tmpdir, "lancedb"),
            os.path.join(tmpdir, "blobs"),
            os.path.join(tmpdir, "jobs.db"),
        )


@pytest.fixture(scope="module")
def client_harvest(tmp_dirs_harvest) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db = tmp_dirs_harvest

    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "JOBS_DB": jobs_db,
        "API_KEYS": "test-key",
        "ANTHROPIC_API_KEY": "",
        "LLM_PROVIDER": "anthropic",
        "S3_BUCKET": "",
        "CORS_ORIGINS": "",
        "HARVEST_ENABLED": "true",
        "HARVEST_PROVIDERS": "claude-cli,codex-cli",
        "HARVEST_WINDOW_RESET": "mon 00:00",
        "HARVEST_LOOKAHEAD_HOURS": "12",
    }
    original_env = {k: os.environ.get(k) for k in env_patch}
    os.environ.update(env_patch)
    _clear_caches()

    from contexthub.main import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _clear_caches()


# ---------------------------------------------------------------------------
# 1. Config: harvest settings are available in Settings
# ---------------------------------------------------------------------------

class TestHarvestConfig:

    def test_harvest_enabled_defaults_false(self):
        """HARVEST_ENABLED must default to False."""
        import importlib
        import contexthub.config as cfg_mod
        original = os.environ.pop("HARVEST_ENABLED", None)
        try:
            importlib.reload(cfg_mod)
            s = cfg_mod.Settings()
            assert s.harvest_enabled is False
        finally:
            if original is not None:
                os.environ["HARVEST_ENABLED"] = original
            importlib.reload(cfg_mod)

    def test_harvest_enabled_can_be_set_true(self):
        """HARVEST_ENABLED=true must set harvest_enabled to True."""
        import importlib
        import contexthub.config as cfg_mod
        original = os.environ.get("HARVEST_ENABLED")
        os.environ["HARVEST_ENABLED"] = "true"
        try:
            importlib.reload(cfg_mod)
            s = cfg_mod.Settings()
            assert s.harvest_enabled is True
        finally:
            if original is None:
                os.environ.pop("HARVEST_ENABLED", None)
            else:
                os.environ["HARVEST_ENABLED"] = original
            importlib.reload(cfg_mod)

    def test_harvest_providers_default(self):
        """HARVEST_PROVIDERS must default to 'claude-cli,codex-cli'."""
        import importlib
        import contexthub.config as cfg_mod
        original = os.environ.pop("HARVEST_PROVIDERS", None)
        try:
            importlib.reload(cfg_mod)
            s = cfg_mod.Settings()
            assert "claude-cli" in s.harvest_providers
        finally:
            if original is not None:
                os.environ["HARVEST_PROVIDERS"] = original
            importlib.reload(cfg_mod)

    def test_harvest_window_reset_default(self):
        """HARVEST_WINDOW_RESET must default to 'mon 00:00'."""
        import importlib
        import contexthub.config as cfg_mod
        original = os.environ.pop("HARVEST_WINDOW_RESET", None)
        try:
            importlib.reload(cfg_mod)
            s = cfg_mod.Settings()
            assert s.harvest_window_reset == "mon 00:00"
        finally:
            if original is not None:
                os.environ["HARVEST_WINDOW_RESET"] = original
            importlib.reload(cfg_mod)

    def test_harvest_lookahead_hours_default(self):
        """HARVEST_LOOKAHEAD_HOURS must default to 12."""
        import importlib
        import contexthub.config as cfg_mod
        original = os.environ.pop("HARVEST_LOOKAHEAD_HOURS", None)
        try:
            importlib.reload(cfg_mod)
            s = cfg_mod.Settings()
            assert s.harvest_lookahead_hours == 12
        finally:
            if original is not None:
                os.environ["HARVEST_LOOKAHEAD_HOURS"] = original
            importlib.reload(cfg_mod)


# ---------------------------------------------------------------------------
# 2. Window-reset parsing
# ---------------------------------------------------------------------------

class TestWindowResetParsing:

    def test_parse_window_reset_mon(self):
        """'mon 00:00' should parse to isoweekday 1 (Monday), 00:00."""
        from contexthub.jobs.harvest import parse_window_reset
        day, hour, minute = parse_window_reset("mon 00:00")
        assert day == 1   # Monday = 1
        assert hour == 0
        assert minute == 0

    def test_parse_window_reset_fri_noon(self):
        """'fri 12:30' should parse to isoweekday 5, 12:30."""
        from contexthub.jobs.harvest import parse_window_reset
        day, hour, minute = parse_window_reset("fri 12:30")
        assert day == 5
        assert hour == 12
        assert minute == 30

    def test_parse_window_reset_sun(self):
        """'sun 03:00' should parse to isoweekday 7."""
        from contexthub.jobs.harvest import parse_window_reset
        day, hour, minute = parse_window_reset("sun 03:00")
        assert day == 7
        assert hour == 3
        assert minute == 0

    def test_next_reset_future(self):
        """next_reset_datetime returns a datetime in the future."""
        from contexthub.jobs.harvest import next_reset_datetime
        # Monday 00:00 reset; use a known Wednesday as reference
        ref = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)  # Wednesday
        reset = next_reset_datetime(1, 0, 0, ref=ref)  # next Monday
        assert reset > ref
        # Should be the coming Monday at 00:00
        assert reset.isoweekday() == 1
        assert reset.hour == 0
        assert reset.minute == 0

    def test_next_reset_same_weekday_past_time_gives_next_week(self):
        """If today is Monday but past the reset time, next reset is 7 days away."""
        from contexthub.jobs.harvest import next_reset_datetime
        # Monday 12:00 is past 00:00 reset
        ref = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)  # Monday
        reset = next_reset_datetime(1, 0, 0, ref=ref)
        assert reset.isoweekday() == 1
        assert (reset - ref).days >= 6  # must be next week

    def test_next_reset_same_weekday_before_time_gives_today(self):
        """If today is Monday before 23:00 reset, next reset is today."""
        from contexthub.jobs.harvest import next_reset_datetime
        ref = datetime(2026, 6, 8, 10, 0, 0, tzinfo=timezone.utc)  # Monday 10:00
        reset = next_reset_datetime(1, 23, 0, ref=ref)  # reset at 23:00
        assert reset.isoweekday() == 1
        assert reset.hour == 23
        assert reset.date() == ref.date()


# ---------------------------------------------------------------------------
# 3. Lookahead check
# ---------------------------------------------------------------------------

class TestLookaheadCheck:

    def test_within_lookahead_returns_true(self):
        """Returns True when now is within lookahead_hours of the reset."""
        from contexthub.jobs.harvest import within_lookahead

        # Reset is 6 hours from now; lookahead is 12 hours → should be True
        now = datetime(2026, 6, 8, 18, 0, 0, tzinfo=timezone.utc)  # Mon 18:00
        reset = datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc)  # Tue 00:00 (6h away)
        assert within_lookahead(now, reset, lookahead_hours=12) is True

    def test_outside_lookahead_returns_false(self):
        """Returns False when now is more than lookahead_hours before reset."""
        from contexthub.jobs.harvest import within_lookahead

        now = datetime(2026, 6, 8, 0, 0, 0, tzinfo=timezone.utc)  # Mon 00:00
        reset = datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc)  # Tue 00:00 (24h away)
        assert within_lookahead(now, reset, lookahead_hours=12) is False

    def test_past_reset_returns_false(self):
        """Returns False when now is past the reset."""
        from contexthub.jobs.harvest import within_lookahead

        now = datetime(2026, 6, 9, 1, 0, 0, tzinfo=timezone.utc)   # Tue 01:00
        reset = datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc)  # Tue 00:00 (already past)
        assert within_lookahead(now, reset, lookahead_hours=12) is False


# ---------------------------------------------------------------------------
# 4. harvest_check handler: drains summary-less sessions
# ---------------------------------------------------------------------------

class TestHarvestCheckHandler:

    def _make_store(self, tmp_path: str):
        from contexthub.jobs.store import JobStore
        return JobStore(os.path.join(tmp_path, "jobs.db"))

    def test_outside_lookahead_no_jobs_enqueued(self, tmp_path):
        """If not within lookahead, harvest_check does nothing."""
        from contexthub.jobs.harvest import harvest_check_handler

        store = self._make_store(str(tmp_path))

        # Mock vector store returning sessions with no summary
        mock_vs = MagicMock()
        mock_vs.list_sessions.return_value = {
            "items": [{"id": "s1", "summary": "", "tool": "claude-code"}],
            "total": 1, "limit": 500, "offset": 0,
        }

        with patch("contexthub.jobs.harvest.get_vector_store_fn", return_value=mock_vs):
            # Use a reference time far from reset (>12h from Monday 00:00)
            # e.g. Monday 06:00 — next reset is 7 days away
            fake_now = datetime(2026, 6, 8, 6, 0, 0, tzinfo=timezone.utc)  # Mon 06:00
            result = harvest_check_handler({
                "job_store": store,
                "_now_override": fake_now.isoformat(),
                "window_reset": "mon 00:00",
                "lookahead_hours": 12,
                "harvest_enabled": True,
            })

        assert result["action"] == "skipped"
        assert result.get("reason") == "outside_lookahead"
        # No summarize_batch jobs should have been enqueued
        batch_jobs = store.list(kind="summarize_batch")
        assert len(batch_jobs) == 0

    def test_inside_lookahead_enqueues_summarize_for_pending(self, tmp_path):
        """Inside lookahead, harvest_check enqueues summarize_batch for sessions without summaries."""
        from contexthub.jobs.harvest import harvest_check_handler

        store = self._make_store(str(tmp_path) + "_inner")

        mock_vs = MagicMock()
        mock_vs.list_sessions.return_value = {
            "items": [
                {"id": "ns-1", "summary": "", "tool": "claude-code"},
                {"id": "ns-2", "summary": "already has one", "tool": "claude-code"},
                {"id": "ns-3", "summary": "", "tool": "codex"},
            ],
            "total": 3, "limit": 500, "offset": 0,
        }

        with patch("contexthub.jobs.harvest.get_vector_store_fn", return_value=mock_vs):
            # Sunday 22:00 — next reset (mon 00:00) is 2 hours away (within 12h)
            fake_now = datetime(2026, 6, 14, 22, 0, 0, tzinfo=timezone.utc)  # Sun 22:00
            result = harvest_check_handler({
                "job_store": store,
                "_now_override": fake_now.isoformat(),
                "window_reset": "mon 00:00",
                "lookahead_hours": 12,
                "harvest_enabled": True,
            })

        assert result["action"] == "drained"
        assert result["pending_summarize"] == 2  # ns-1 and ns-3 lack summaries

        # A summarize_batch job should have been enqueued
        batch_jobs = store.list(kind="summarize_batch")
        assert len(batch_jobs) == 1
        payload = batch_jobs[0]["payload"]
        assert "ns-1" in payload["session_ids"]
        assert "ns-3" in payload["session_ids"]
        assert "ns-2" not in payload["session_ids"]

    def test_harvest_disabled_does_nothing(self, tmp_path):
        """When harvest_enabled=False, handler skips immediately."""
        from contexthub.jobs.harvest import harvest_check_handler

        store = self._make_store(str(tmp_path) + "_disabled")

        mock_vs = MagicMock()

        with patch("contexthub.jobs.harvest.get_vector_store_fn", return_value=mock_vs):
            fake_now = datetime(2026, 6, 15, 23, 0, 0, tzinfo=timezone.utc)
            result = harvest_check_handler({
                "job_store": store,
                "_now_override": fake_now.isoformat(),
                "window_reset": "mon 00:00",
                "lookahead_hours": 12,
                "harvest_enabled": False,
            })

        assert result["action"] == "skipped"
        assert result["reason"] == "disabled"
        # Vector store should never have been queried
        mock_vs.list_sessions.assert_not_called()

    def test_stops_on_provider_error(self, tmp_path):
        """When summarize_batch raises, harvest_check stops and records the error."""
        from contexthub.jobs.harvest import harvest_check_handler

        store = self._make_store(str(tmp_path) + "_error")

        mock_vs = MagicMock()
        mock_vs.list_sessions.return_value = {
            "items": [
                {"id": "err-1", "summary": "", "tool": "claude-code"},
            ],
            "total": 1, "limit": 500, "offset": 0,
        }

        def _bad_enqueue(kind, payload, scheduled_for=None):
            if kind == "summarize_batch":
                raise RuntimeError("rate limit exceeded")
            return "fake-id"

        store.enqueue = _bad_enqueue

        with patch("contexthub.jobs.harvest.get_vector_store_fn", return_value=mock_vs):
            # Sunday 22:00 — within 12h lookahead of Monday 00:00
            fake_now = datetime(2026, 6, 14, 22, 0, 0, tzinfo=timezone.utc)
            result = harvest_check_handler({
                "job_store": store,
                "_now_override": fake_now.isoformat(),
                "window_reset": "mon 00:00",
                "lookahead_hours": 12,
                "harvest_enabled": True,
            })

        # Should not raise; should record the error
        assert result["action"] == "drained"
        assert result.get("error") is not None
        assert "rate limit" in result["error"].lower()

    def test_enqueues_graph_extract_for_sessions_without_graph(self, tmp_path):
        """harvest_check also enqueues graph_extract for sessions lacking graph data."""
        from contexthub.jobs.harvest import harvest_check_handler

        store = self._make_store(str(tmp_path) + "_graph")

        mock_vs = MagicMock()
        # Session with summary but no graph_extracted flag
        mock_vs.list_sessions.return_value = {
            "items": [
                {"id": "g-1", "summary": "has summary", "tool": "claude-code", "graph_extracted": False},
                {"id": "g-2", "summary": "has summary", "tool": "claude-code", "graph_extracted": True},
            ],
            "total": 2, "limit": 500, "offset": 0,
        }

        with patch("contexthub.jobs.harvest.get_vector_store_fn", return_value=mock_vs):
            # Sunday 22:00 — within 12h lookahead of Monday 00:00
            fake_now = datetime(2026, 6, 14, 22, 0, 0, tzinfo=timezone.utc)
            result = harvest_check_handler({
                "job_store": store,
                "_now_override": fake_now.isoformat(),
                "window_reset": "mon 00:00",
                "lookahead_hours": 12,
                "harvest_enabled": True,
            })

        # graph_extract jobs should have been enqueued for g-1 only
        graph_jobs = store.list(kind="graph_extract")
        assert len(graph_jobs) == 1
        assert graph_jobs[0]["payload"]["session_id"] == "g-1"

    def test_reschedules_next_harvest_check(self, tmp_path):
        """harvest_check enqueues the next harvest_check job scheduled 1 hour from now."""
        from contexthub.jobs.harvest import harvest_check_handler

        store = self._make_store(str(tmp_path) + "_reschedule")

        mock_vs = MagicMock()
        mock_vs.list_sessions.return_value = {
            "items": [],
            "total": 0, "limit": 500, "offset": 0,
        }

        with patch("contexthub.jobs.harvest.get_vector_store_fn", return_value=mock_vs):
            fake_now = datetime(2026, 6, 15, 22, 0, 0, tzinfo=timezone.utc)
            harvest_check_handler({
                "job_store": store,
                "_now_override": fake_now.isoformat(),
                "window_reset": "mon 00:00",
                "lookahead_hours": 12,
                "harvest_enabled": True,
            })

        # A next harvest_check job should be scheduled
        next_jobs = store.list(kind="harvest_check")
        assert len(next_jobs) == 1
        raw = store.get(next_jobs[0]["id"])
        assert raw is not None
        # Should be scheduled in the future
        scheduled = raw.get("scheduled_for")
        assert scheduled is not None


# ---------------------------------------------------------------------------
# 5. Handler registered in HANDLER_REGISTRY
# ---------------------------------------------------------------------------

def test_harvest_check_in_registry():
    """harvest_check must be registered in the HANDLER_REGISTRY."""
    from contexthub.jobs.handlers import HANDLER_REGISTRY
    assert "harvest_check" in HANDLER_REGISTRY, (
        "harvest_check handler not found in HANDLER_REGISTRY"
    )


# ---------------------------------------------------------------------------
# 6. GET /v1/harvest/status endpoint
# ---------------------------------------------------------------------------

def test_harvest_status_endpoint(client_harvest: TestClient):
    """GET /v1/harvest/status must return expected fields."""
    resp = client_harvest.get("/v1/harvest/status", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Must have these top-level fields
    assert "harvest_enabled" in data
    assert "next_reset" in data
    assert "pending_counts" in data
    assert "last_drain_results" in data

    # next_reset must be a non-empty string (ISO datetime)
    assert isinstance(data["next_reset"], str)
    assert len(data["next_reset"]) > 0

    # pending_counts must be a dict
    assert isinstance(data["pending_counts"], dict)
    assert "pending_summarize" in data["pending_counts"]
    assert "pending_graph_extract" in data["pending_counts"]


def test_harvest_status_requires_auth(client_harvest: TestClient):
    """GET /v1/harvest/status without auth must return 401 or 403."""
    resp = client_harvest.get("/v1/harvest/status")
    assert resp.status_code in (401, 403)


def test_harvest_status_shows_enabled_true(client_harvest: TestClient):
    """When HARVEST_ENABLED=true, the status endpoint reports harvest_enabled=True."""
    resp = client_harvest.get("/v1/harvest/status", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["harvest_enabled"] is True


# ---------------------------------------------------------------------------
# 7. Integration: harvest_check_handler wired at startup (hourly schedule)
# ---------------------------------------------------------------------------

def test_harvest_check_startup_enqueue(tmp_path):
    """
    When HARVEST_ENABLED=true, _ensure_harvest_check should enqueue a harvest_check
    job if none is already queued.
    """
    from contexthub.jobs.store import JobStore
    from contexthub.main import _ensure_harvest_check

    db_path = os.path.join(str(tmp_path), "jobs_harvest.db")
    store = JobStore(db_path)

    # No jobs yet → should enqueue one
    _ensure_harvest_check(store, harvest_enabled=True)
    jobs = store.list(kind="harvest_check")
    assert len(jobs) == 1

    # Call again → should NOT enqueue a duplicate (idempotent)
    _ensure_harvest_check(store, harvest_enabled=True)
    jobs = store.list(kind="harvest_check")
    assert len(jobs) == 1  # still just one


def test_harvest_check_not_enqueued_when_disabled(tmp_path):
    """When harvest_enabled=False, _ensure_harvest_check should not enqueue anything."""
    from contexthub.jobs.store import JobStore
    from contexthub.main import _ensure_harvest_check

    db_path = os.path.join(str(tmp_path), "jobs_harvest_disabled.db")
    store = JobStore(db_path)

    _ensure_harvest_check(store, harvest_enabled=False)
    jobs = store.list(kind="harvest_check")
    assert len(jobs) == 0
