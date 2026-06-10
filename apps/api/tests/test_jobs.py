"""Tests for Task 5: Jobs subsystem (async work off the request path).

Covers:
  1. JobStore: enqueue → claim_next → complete/fail status transitions.
  2. Worker: processes a test handler end-to-end.
  3. Ingest with summarize=true enqueues a job and returns fast with job_id.
  4. GET /v1/jobs/{id} and GET /v1/jobs?status=&kind= list/inspect jobs.
  5. scheduled_for: future jobs are skipped by claim_next.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from typing import Generator

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


# ---------------------------------------------------------------------------
# JobStore unit tests (no HTTP)
# ---------------------------------------------------------------------------

class TestJobStore:
    def setup_method(self):
        import tempfile as _t
        self._tmpdir = _t.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "jobs.db")
        # Import after clearing any cached state
        from contexthub.jobs.store import JobStore
        self.store = JobStore(db_path)

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_enqueue_returns_id(self):
        job_id = self.store.enqueue(kind="test_job", payload={"key": "val"})
        assert isinstance(job_id, str)
        assert len(job_id) > 0

    def test_get_by_id(self):
        job_id = self.store.enqueue(kind="test_job", payload={"x": 1})
        job = self.store.get(job_id)
        assert job is not None
        assert job["id"] == job_id
        assert job["kind"] == "test_job"
        assert job["status"] == "queued"
        assert job["payload"] == {"x": 1}

    def test_initial_status_is_queued(self):
        job_id = self.store.enqueue(kind="my_kind", payload={})
        job = self.store.get(job_id)
        assert job["status"] == "queued"

    def test_claim_next_returns_queued_job(self):
        self.store.enqueue(kind="kind_a", payload={"a": 1})
        job = self.store.claim_next()
        assert job is not None
        assert job["status"] == "running"

    def test_claim_next_no_job_returns_none(self):
        job = self.store.claim_next()
        assert job is None

    def test_complete_sets_status_done(self):
        job_id = self.store.enqueue(kind="k", payload={})
        self.store.claim_next()
        self.store.complete(job_id, result={"output": "ok"})
        job = self.store.get(job_id)
        assert job["status"] == "done"
        assert job["result"] == {"output": "ok"}
        assert job["finished_at"] is not None

    def test_fail_sets_status_error(self):
        job_id = self.store.enqueue(kind="k", payload={})
        self.store.claim_next()
        self.store.fail(job_id, error="something went wrong")
        job = self.store.get(job_id)
        assert job["status"] == "error"
        assert job["error"] == "something went wrong"
        assert job["finished_at"] is not None

    def test_claim_next_skips_running_job(self):
        """A job that is 'running' must not be claimed again."""
        id1 = self.store.enqueue(kind="k", payload={})
        id2 = self.store.enqueue(kind="k", payload={})
        j1 = self.store.claim_next()
        assert j1 is not None
        assert j1["id"] in (id1, id2)
        j2 = self.store.claim_next()
        assert j2 is not None
        assert j2["id"] != j1["id"]
        j3 = self.store.claim_next()
        assert j3 is None  # no more queued

    def test_scheduled_for_future_is_skipped(self):
        """Jobs with a future scheduled_for must not be returned by claim_next."""
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.store.enqueue(kind="k", payload={}, scheduled_for=future)
        job = self.store.claim_next()
        assert job is None  # future job should be skipped

    def test_scheduled_for_past_is_claimed(self):
        """Jobs with a past scheduled_for must be returned by claim_next."""
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        job_id = self.store.enqueue(kind="k", payload={}, scheduled_for=past)
        job = self.store.claim_next()
        assert job is not None
        assert job["id"] == job_id

    def test_list_by_status(self):
        id1 = self.store.enqueue(kind="k", payload={})
        id2 = self.store.enqueue(kind="k", payload={})
        self.store.enqueue(kind="k", payload={})
        # Claim and complete first
        j = self.store.claim_next()
        self.store.complete(j["id"], result={})

        queued = self.store.list(status="queued")
        done = self.store.list(status="done")
        assert len(done) == 1
        assert len(queued) == 2

    def test_list_by_kind(self):
        self.store.enqueue(kind="alpha", payload={})
        self.store.enqueue(kind="alpha", payload={})
        self.store.enqueue(kind="beta", payload={})
        alphas = self.store.list(kind="alpha")
        betas = self.store.list(kind="beta")
        assert len(alphas) == 2
        assert len(betas) == 1

    def test_list_by_status_and_kind(self):
        self.store.enqueue(kind="alpha", payload={})
        self.store.enqueue(kind="beta", payload={})
        j = self.store.claim_next()
        self.store.complete(j["id"], result={})

        done_alpha = self.store.list(status="done", kind="alpha")
        done_beta = self.store.list(status="done", kind="beta")
        # Only one is done — assert the right kind
        assert len(done_alpha) + len(done_beta) == 1


# ---------------------------------------------------------------------------
# Worker unit test (asyncio, no HTTP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_processes_job():
    """Worker processes a registered handler and transitions status to 'done'."""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "jobs.db")
        from contexthub.jobs.store import JobStore
        from contexthub.jobs.worker import Worker

        store = JobStore(db_path)
        results = []

        def _test_handler(payload: dict) -> dict:
            results.append(payload)
            return {"handled": True, "echo": payload.get("msg")}

        worker = Worker(store=store, handlers={"test_echo": _test_handler}, poll_interval=0.05)

        job_id = store.enqueue(kind="test_echo", payload={"msg": "hello"})

        # Start worker, let it run one cycle, then stop
        task = asyncio.create_task(worker.run())
        # Give worker time to pick up and process the job
        for _ in range(20):
            await asyncio.sleep(0.05)
            job = store.get(job_id)
            if job and job["status"] in ("done", "error"):
                break

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        job = store.get(job_id)
        assert job is not None
        assert job["status"] == "done", f"Expected done, got {job['status']} — error: {job.get('error')}"
        assert job["result"] == {"handled": True, "echo": "hello"}
        assert results == [{"msg": "hello"}]


@pytest.mark.asyncio
async def test_worker_marks_job_error_on_handler_exception():
    """Worker marks a job as 'error' when the handler raises an exception."""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "jobs.db")
        from contexthub.jobs.store import JobStore
        from contexthub.jobs.worker import Worker

        store = JobStore(db_path)

        def _failing_handler(payload: dict) -> dict:
            raise RuntimeError("intentional test failure")

        worker = Worker(store=store, handlers={"fail_job": _failing_handler}, poll_interval=0.05)
        job_id = store.enqueue(kind="fail_job", payload={})

        task = asyncio.create_task(worker.run())
        for _ in range(20):
            await asyncio.sleep(0.05)
            job = store.get(job_id)
            if job and job["status"] in ("done", "error"):
                break

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        job = store.get(job_id)
        assert job["status"] == "error"
        assert "intentional test failure" in job["error"]


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tmp_dirs_jobs():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield (
            os.path.join(tmpdir, "lancedb"),
            os.path.join(tmpdir, "blobs"),
            os.path.join(tmpdir, "jobs.db"),
        )


@pytest.fixture(scope="module")
def client_jobs(tmp_dirs_jobs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db = tmp_dirs_jobs

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


AUTH = {"Authorization": "Bearer test-key"}


def _make_session(session_id: str) -> dict:
    return {
        "session": {
            "id": session_id,
            "tool": "claude-code",
            "title": f"Session {session_id}",
            "cwd": "/Users/test/proj",
            "project": "proj",
            "started_at": "2026-06-09T10:00:00Z",
            "ended_at": "2026-06-09T10:45:00Z",
            "message_count": 4,
            "models": ["claude-sonnet-4-6"],
            "tokens": {"input": 1000, "output": 200},
            "preview": "Some preview text",
            "file_path": f"/Users/test/.claude/projects/proj/{session_id}.jsonl",
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "text": "Hello world from " + session_id,
                    "timestamp": "2026-06-09T10:00:00Z",
                    "model": None,
                },
                {
                    "id": "m2",
                    "role": "assistant",
                    "text": "This is the assistant reply for " + session_id,
                    "timestamp": "2026-06-09T10:01:00Z",
                    "model": "claude-sonnet-4-6",
                },
            ],
        },
        "category": "engineering",
        "visibility": "company",
        "author": {"id": "u_test", "email": "test@example.com", "name": "Test User"},
        "redacted": True,
    }


def test_ingest_with_summarize_true_returns_job_id(client_jobs: TestClient):
    """POST /v1/sessions?summarize=true must return a job_id and not block."""
    payload = _make_session("job-ingest-001")
    resp = client_jobs.post("/v1/sessions?summarize=true", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "job_id" in data, f"Expected job_id in response, got: {list(data.keys())}"
    assert data["job_id"] is not None


def test_ingest_without_summarize_no_job_id(client_jobs: TestClient):
    """POST /v1/sessions without summarize=true should return job_id=None."""
    payload = _make_session("job-ingest-002")
    resp = client_jobs.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # job_id should be None/absent when summarize is not requested
    assert data.get("job_id") is None


def test_get_job_by_id(client_jobs: TestClient):
    """GET /v1/jobs/{id} must return a job record."""
    payload = _make_session("job-get-001")
    resp = client_jobs.post("/v1/sessions?summarize=true", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    resp2 = client_jobs.get(f"/v1/jobs/{job_id}", headers=AUTH)
    assert resp2.status_code == 200, resp2.text
    job = resp2.json()
    assert job["id"] == job_id
    assert job["kind"] == "summarize_session"
    assert job["status"] in ("queued", "running", "done", "error")


def test_get_job_not_found(client_jobs: TestClient):
    """GET /v1/jobs/{id} with unknown id must return 404."""
    resp = client_jobs.get("/v1/jobs/nonexistent-job-id-xyz", headers=AUTH)
    assert resp.status_code == 404


def test_list_jobs(client_jobs: TestClient):
    """GET /v1/jobs must return a list of job records."""
    payload = _make_session("job-list-001")
    client_jobs.post("/v1/sessions?summarize=true", json=payload, headers=AUTH)

    resp = client_jobs.get("/v1/jobs", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1


def test_list_jobs_filter_by_status(client_jobs: TestClient):
    """GET /v1/jobs?status=queued must filter by status."""
    payload = _make_session("job-list-002")
    client_jobs.post("/v1/sessions?summarize=true", json=payload, headers=AUTH)

    resp = client_jobs.get("/v1/jobs?status=queued", headers=AUTH)
    assert resp.status_code == 200, resp.text
    jobs = resp.json()
    # All returned jobs must have queued status (or none if worker ran them already)
    for job in jobs:
        assert job["status"] == "queued"


def test_list_jobs_filter_by_kind(client_jobs: TestClient):
    """GET /v1/jobs?kind=summarize_session must filter by kind."""
    payload = _make_session("job-list-003")
    client_jobs.post("/v1/sessions?summarize=true", json=payload, headers=AUTH)

    resp = client_jobs.get("/v1/jobs?kind=summarize_session", headers=AUTH)
    assert resp.status_code == 200, resp.text
    jobs = resp.json()
    assert len(jobs) >= 1
    for job in jobs:
        assert job["kind"] == "summarize_session"


def test_ingest_response_has_job_id_field(client_jobs: TestClient):
    """IngestResponse schema must include the job_id field."""
    payload = _make_session("job-schema-001")
    resp = client_jobs.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    # job_id field must exist in the response (may be None)
    assert "job_id" in data, f"job_id field missing from IngestResponse: {list(data.keys())}"
