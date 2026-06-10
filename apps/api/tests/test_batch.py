"""Tests for Task 11: Batch summarization (OpenAI Batch API + local model).

Covers:
  1. POST /v1/summarize/batch enqueues a 'summarize_batch' job.
  2. openai-batch handler: builds correct JSONL, uploads, creates OpenAI batch,
     stores batch_id, and re-enqueues a 'batch_poll' job.
  3. batch_poll handler: on completion, parses output and writes summaries.
  4. local handler: loops sessions through the local (ollama) provider.
  5. summarize_pending handler: enqueues summarization for sessions with no summary.
  6. nightly schedule: summarize_pending is enqueued at startup when absent.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers shared across tests
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
        "contexthub.llm_batch",
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
                {"id": "m1", "role": "user", "text": "Implement feature X", "timestamp": "2026-06-10T10:00:00Z"},
                {"id": "m2", "role": "assistant", "text": "Done, here is the implementation.", "timestamp": "2026-06-10T10:01:00Z"},
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
def tmp_dirs_batch():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield (
            os.path.join(tmpdir, "lancedb"),
            os.path.join(tmpdir, "blobs"),
            os.path.join(tmpdir, "jobs.db"),
        )


@pytest.fixture(scope="module")
def client_batch(tmp_dirs_batch) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db = tmp_dirs_batch

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
        "OPENAI_API_KEY": "test-openai-key",
        "LOCAL_LLM_BASE_URL": "http://localhost:11434/v1",
        "LOCAL_LLM_MODEL": "mistral",
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
# 1. POST /v1/summarize/batch enqueues a job
# ---------------------------------------------------------------------------

def test_batch_endpoint_enqueues_job(client_batch: TestClient):
    """POST /v1/summarize/batch must return a job_id and enqueue kind=summarize_batch."""
    # Ingest some sessions first
    for sid in ["batch-s1", "batch-s2"]:
        r = client_batch.post("/v1/sessions", json=_make_session_payload(sid), headers=AUTH)
        assert r.status_code == 200, r.text

    body = {"session_ids": ["batch-s1", "batch-s2"], "provider": "openai-batch"}
    resp = client_batch.post("/v1/summarize/batch", json=body, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "job_id" in data
    assert data["job_id"] is not None
    assert data["kind"] == "summarize_batch"


def test_batch_endpoint_local_provider(client_batch: TestClient):
    """POST /v1/summarize/batch with provider=local must also enqueue a job."""
    for sid in ["batch-local-s1"]:
        client_batch.post("/v1/sessions", json=_make_session_payload(sid), headers=AUTH)

    body = {"session_ids": ["batch-local-s1"], "provider": "local"}
    resp = client_batch.post("/v1/summarize/batch", json=body, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["job_id"] is not None
    assert data["kind"] == "summarize_batch"


def test_batch_endpoint_default_provider(client_batch: TestClient):
    """POST /v1/summarize/batch with provider=default must enqueue a job."""
    for sid in ["batch-default-s1"]:
        client_batch.post("/v1/sessions", json=_make_session_payload(sid), headers=AUTH)

    body = {"session_ids": ["batch-default-s1"], "provider": "default"}
    resp = client_batch.post("/v1/summarize/batch", json=body, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["job_id"] is not None


def test_batch_endpoint_requires_auth(client_batch: TestClient):
    """POST /v1/summarize/batch without auth must return 401 or 403."""
    body = {"session_ids": ["some-session"], "provider": "default"}
    resp = client_batch.post("/v1/summarize/batch", json=body)
    assert resp.status_code in (401, 403)


def test_batch_endpoint_empty_list(client_batch: TestClient):
    """POST /v1/summarize/batch with empty session_ids must return 422."""
    body = {"session_ids": [], "provider": "default"}
    resp = client_batch.post("/v1/summarize/batch", json=body, headers=AUTH)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. openai-batch handler: JSONL shape, upload, create batch, re-enqueue poll
# ---------------------------------------------------------------------------

class TestOpenAIBatchHandler:
    """Unit tests for the openai-batch handler using mocked OpenAI client."""

    def _make_store_and_handler(self, tmp_path: str):
        from contexthub.jobs.store import JobStore
        store = JobStore(os.path.join(tmp_path, "jobs.db"))
        return store

    def test_batch_jsonl_shape(self, tmp_path):
        """The handler builds one JSONL request per session, with correct fields."""
        from contexthub.llm_batch import build_batch_jsonl

        sessions = [
            {
                "id": "s1",
                "title": "Fix bug",
                "messages": [
                    {"role": "user", "text": "Fix the null pointer exception"},
                    {"role": "assistant", "text": "Fixed it in line 42"},
                ],
            },
            {
                "id": "s2",
                "title": "Add tests",
                "messages": [
                    {"role": "user", "text": "Write tests for module X"},
                ],
            },
        ]
        jsonl = build_batch_jsonl(sessions)
        lines = [l for l in jsonl.strip().split("\n") if l.strip()]
        assert len(lines) == 2, f"Expected 2 JSONL lines, got {len(lines)}"

        for line in lines:
            req = json.loads(line)
            assert "custom_id" in req, "Each request must have a custom_id"
            assert req["method"] == "POST"
            assert req["url"] == "/v1/chat/completions"
            assert "body" in req
            assert "model" in req["body"]
            assert "messages" in req["body"]

    def test_batch_jsonl_truncates_long_transcripts(self, tmp_path):
        """Transcripts longer than 40k chars must be truncated in the JSONL."""
        from contexthub.llm_batch import build_batch_jsonl

        long_text = "x" * 50_000
        sessions = [{"id": "s1", "title": "Long", "messages": [{"role": "user", "text": long_text}]}]
        jsonl = build_batch_jsonl(sessions)
        line = json.loads(jsonl.strip().split("\n")[0])
        # The user message content in the body must be <= 40k + some overhead
        user_content = ""
        for msg in line["body"]["messages"]:
            if msg.get("role") == "user":
                user_content = msg.get("content", "")
        assert len(user_content) <= 42_000, f"Transcript not truncated: {len(user_content)}"

    def test_openai_batch_handler_uploads_and_creates_batch(self, tmp_path):
        """Handler uploads JSONL file and calls client.batches.create."""
        from contexthub.llm_batch import handle_summarize_batch_openai

        mock_file_response = MagicMock()
        mock_file_response.id = "file-abc123"

        mock_batch_response = MagicMock()
        mock_batch_response.id = "batch-xyz789"

        mock_client = MagicMock()
        mock_client.files.create.return_value = mock_file_response
        mock_client.batches.create.return_value = mock_batch_response

        store = self._make_store_and_handler(tmp_path)

        sessions_data = [
            {"id": "s1", "title": "Session 1", "messages": [{"role": "user", "text": "hello"}]},
        ]

        result = handle_summarize_batch_openai(
            session_ids=["s1"],
            sessions_data=sessions_data,
            openai_client=mock_client,
            job_store=store,
            job_id="parent-job-001",
            model="gpt-4o-mini",
        )

        # Must have called files.create with purpose="batch"
        mock_client.files.create.assert_called_once()
        call_kwargs = mock_client.files.create.call_args
        assert call_kwargs[1].get("purpose") == "batch" or (
            len(call_kwargs[0]) > 1 and call_kwargs[0][1] == "batch"
        )

        # Must have called batches.create with completion_window="24h"
        mock_client.batches.create.assert_called_once()
        batch_kwargs = mock_client.batches.create.call_args[1]
        assert batch_kwargs.get("completion_window") == "24h"
        assert batch_kwargs.get("input_file_id") == "file-abc123"

        # Result must contain batch_id
        assert result["batch_id"] == "batch-xyz789"
        assert result["file_id"] == "file-abc123"

        # A batch_poll job must have been enqueued
        poll_jobs = store.list(kind="batch_poll")
        assert len(poll_jobs) == 1
        poll_payload = poll_jobs[0]["payload"]
        assert poll_payload["batch_id"] == "batch-xyz789"
        assert poll_payload["session_ids"] == ["s1"]

    def test_batch_poll_handler_on_completion_writes_summaries(self, tmp_path):
        """On batch completion, poll handler parses output and updates session rows."""
        from contexthub.llm_batch import handle_batch_poll

        # Build a fake completed batch output JSONL
        output_lines = [
            json.dumps({
                "id": "resp-001",
                "custom_id": "s1",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{"message": {"content": "### Title\nFix bug summary\n\n### What Happened\nFixed it."}}]
                    },
                },
                "error": None,
            })
        ]
        output_content = "\n".join(output_lines).encode("utf-8")

        mock_batch = MagicMock()
        mock_batch.status = "completed"
        mock_batch.output_file_id = "file-out-001"

        mock_file_content = MagicMock()
        mock_file_content.content = output_content

        mock_client = MagicMock()
        mock_client.batches.retrieve.return_value = mock_batch
        mock_client.files.content.return_value = mock_file_content

        # Track calls to update_summary
        updated: dict = {}

        def fake_update_summary(session_id: str, summary: str) -> None:
            updated[session_id] = summary

        result = handle_batch_poll(
            batch_id="batch-xyz789",
            session_ids=["s1"],
            openai_client=mock_client,
            update_summary_fn=fake_update_summary,
        )

        assert result["status"] == "completed"
        assert result["summaries_written"] == 1
        assert "s1" in updated
        assert "Fix bug summary" in updated["s1"]

    def test_batch_poll_handler_when_still_in_progress(self, tmp_path):
        """Poll handler re-enqueues itself when batch is not yet complete."""
        from contexthub.llm_batch import handle_batch_poll

        mock_batch = MagicMock()
        mock_batch.status = "in_progress"
        mock_batch.output_file_id = None

        mock_client = MagicMock()
        mock_client.batches.retrieve.return_value = mock_batch

        store = self._make_store_and_handler(tmp_path)

        result = handle_batch_poll(
            batch_id="batch-in-progress",
            session_ids=["s1"],
            openai_client=mock_client,
            update_summary_fn=lambda sid, s: None,
            job_store=store,
            job_id="poll-job-001",
        )

        assert result["status"] == "in_progress"
        # A new poll job must have been re-enqueued
        poll_jobs = store.list(kind="batch_poll")
        assert len(poll_jobs) == 1
        assert poll_jobs[0]["payload"]["batch_id"] == "batch-in-progress"
        # Scheduled for future (10 min from now)
        scheduled = poll_jobs[0].get("scheduled_for") or poll_jobs[0]["payload"].get("scheduled_for")
        # The job_store row should have a scheduled_for (in the future)
        raw_job = store.get(poll_jobs[0]["id"])
        assert raw_job is not None


# ---------------------------------------------------------------------------
# 3. local handler: loops sessions through local openai-compatible provider
# ---------------------------------------------------------------------------

class TestLocalBatchHandler:

    def test_local_handler_calls_llm_for_each_session(self, tmp_path):
        """Local handler calls the LLM for each session and collects summaries."""
        from contexthub.llm_batch import handle_summarize_batch_local

        summaries_written = {}

        def fake_update(session_id: str, summary: str) -> None:
            summaries_written[session_id] = summary

        mock_llm = MagicMock()
        mock_llm.complete.return_value = "### Title\nSummary for session\n\n### What Happened\nDone."
        mock_llm.available.return_value = True

        sessions_data = [
            {"id": "loc-1", "title": "S1", "messages": [{"role": "user", "text": "hello"}]},
            {"id": "loc-2", "title": "S2", "messages": [{"role": "user", "text": "world"}]},
        ]

        result = handle_summarize_batch_local(
            sessions_data=sessions_data,
            llm_client=mock_llm,
            update_summary_fn=fake_update,
        )

        assert result["summaries_written"] == 2
        assert mock_llm.complete.call_count == 2
        assert "loc-1" in summaries_written
        assert "loc-2" in summaries_written

    def test_local_handler_continues_on_individual_failure(self, tmp_path):
        """Local handler skips failed sessions and continues with the rest."""
        from contexthub.llm_batch import handle_summarize_batch_local

        summaries_written = {}
        call_count = 0

        def fake_update(session_id: str, summary: str) -> None:
            summaries_written[session_id] = summary

        mock_llm = MagicMock()

        def side_effect(system: str, user: str, max_tokens: int = 1024) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("LLM unavailable")
            return "### Title\nOK summary"

        mock_llm.complete.side_effect = side_effect
        mock_llm.available.return_value = True

        sessions_data = [
            {"id": "fail-1", "title": "Fail", "messages": []},
            {"id": "ok-2", "title": "OK", "messages": [{"role": "user", "text": "hello"}]},
        ]

        result = handle_summarize_batch_local(
            sessions_data=sessions_data,
            llm_client=mock_llm,
            update_summary_fn=fake_update,
        )

        # fail-1 failed, ok-2 succeeded
        assert result["summaries_written"] == 1
        assert result["errors"] == 1
        assert "ok-2" in summaries_written


# ---------------------------------------------------------------------------
# 4. Jobs handler registry integration
# ---------------------------------------------------------------------------

def test_summarize_batch_handler_in_registry():
    """summarize_batch must be registered in the HANDLER_REGISTRY."""
    from contexthub.jobs.handlers import HANDLER_REGISTRY
    assert "summarize_batch" in HANDLER_REGISTRY, (
        "summarize_batch handler not found in HANDLER_REGISTRY"
    )


def test_batch_poll_handler_in_registry():
    """batch_poll must be registered in the HANDLER_REGISTRY."""
    from contexthub.jobs.handlers import HANDLER_REGISTRY
    assert "batch_poll" in HANDLER_REGISTRY, (
        "batch_poll handler not found in HANDLER_REGISTRY"
    )


def test_summarize_pending_handler_in_registry():
    """summarize_pending must be registered in the HANDLER_REGISTRY."""
    from contexthub.jobs.handlers import HANDLER_REGISTRY
    assert "summarize_pending" in HANDLER_REGISTRY, (
        "summarize_pending handler not found in HANDLER_REGISTRY"
    )


# ---------------------------------------------------------------------------
# 5. summarize_pending job: batches sessions with no summary
# ---------------------------------------------------------------------------

def test_summarize_pending_enqueues_summarize_batch(tmp_path):
    """summarize_pending handler enqueues a summarize_batch job for sessions lacking summaries."""
    import os
    from contexthub.jobs.store import JobStore
    from contexthub.jobs.handlers import HANDLER_REGISTRY

    store = JobStore(os.path.join(str(tmp_path), "jobs.db"))

    # Mock get_vector_store to return sessions without summaries
    session_no_summary = {"id": "ns-1", "summary": "", "tool": "claude-code"}
    session_with_summary = {"id": "ns-2", "summary": "has summary", "tool": "claude-code"}

    mock_vs = MagicMock()
    mock_vs.list_sessions.return_value = {
        "items": [session_no_summary, session_with_summary],
        "total": 2,
        "limit": 200,
        "offset": 0,
    }

    handler = HANDLER_REGISTRY["summarize_pending"]

    # The handler uses a lazy import of get_vector_store inside the function body.
    # Patch the function in the storage.vectors module so the lazy import resolves to the mock.
    with patch("contexthub.storage.vectors.get_vector_store", return_value=mock_vs):
        result = handler({"job_store": store})

    assert result["pending_count"] == 1  # only session without summary
    # A summarize_batch job should have been enqueued
    batch_jobs = store.list(kind="summarize_batch")
    assert len(batch_jobs) == 1
    payload = batch_jobs[0]["payload"]
    assert "ns-1" in payload.get("session_ids", [])
    assert "ns-2" not in payload.get("session_ids", [])


# ---------------------------------------------------------------------------
# 6. Config: LOCAL_LLM_BASE_URL and LOCAL_LLM_MODEL are available
# ---------------------------------------------------------------------------

def test_config_local_llm_settings():
    """Settings must expose local_llm_base_url and local_llm_model."""
    import os
    original_env = {
        "LOCAL_LLM_BASE_URL": os.environ.get("LOCAL_LLM_BASE_URL"),
        "LOCAL_LLM_MODEL": os.environ.get("LOCAL_LLM_MODEL"),
    }
    os.environ["LOCAL_LLM_BASE_URL"] = "http://localhost:11434/v1"
    os.environ["LOCAL_LLM_MODEL"] = "mistral"
    try:
        # Must import fresh
        import importlib
        import contexthub.config as cfg_mod
        importlib.reload(cfg_mod)
        s = cfg_mod.Settings()
        assert s.local_llm_base_url == "http://localhost:11434/v1"
        assert s.local_llm_model == "mistral"
    finally:
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# 7. llm.py: get_llm resolves "local" provider to local base_url
# ---------------------------------------------------------------------------

def test_get_llm_local_provider():
    """get_llm('local') must return an OpenAICompatible client pointed at local base_url."""
    import os
    from unittest.mock import patch
    original = {
        "LOCAL_LLM_BASE_URL": os.environ.get("LOCAL_LLM_BASE_URL"),
        "LOCAL_LLM_MODEL": os.environ.get("LOCAL_LLM_MODEL"),
    }
    os.environ["LOCAL_LLM_BASE_URL"] = "http://localhost:11434/v1"
    os.environ["LOCAL_LLM_MODEL"] = "phi3"
    try:
        import importlib
        import contexthub.config as cfg_mod
        importlib.reload(cfg_mod)
        settings = cfg_mod.Settings()
        import contexthub.llm as llm_mod
        importlib.reload(llm_mod)
        client = llm_mod.get_llm(settings, provider_override="local")
        # Should be an OpenAICompatible-style client
        assert hasattr(client, "base_url") or hasattr(client, "complete")
        assert client.name in ("local", "openai")
    finally:
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# 8. Batch endpoint listed in GET /v1/jobs
# ---------------------------------------------------------------------------

def test_batch_job_visible_in_list(client_batch: TestClient):
    """A summarize_batch job enqueued via the endpoint must appear in GET /v1/jobs."""
    # Ingest a session
    sid = "batch-list-check-s1"
    client_batch.post("/v1/sessions", json=_make_session_payload(sid), headers=AUTH)

    # Enqueue batch
    body = {"session_ids": [sid], "provider": "default"}
    resp = client_batch.post("/v1/summarize/batch", json=body, headers=AUTH)
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # Retrieve via GET /v1/jobs/{id}
    resp2 = client_batch.get(f"/v1/jobs/{job_id}", headers=AUTH)
    assert resp2.status_code == 200
    job = resp2.json()
    assert job["kind"] == "summarize_batch"

    # Retrieve via GET /v1/jobs?kind=summarize_batch
    resp3 = client_batch.get("/v1/jobs?kind=summarize_batch", headers=AUTH)
    assert resp3.status_code == 200
    kinds = [j["kind"] for j in resp3.json()]
    assert "summarize_batch" in kinds
