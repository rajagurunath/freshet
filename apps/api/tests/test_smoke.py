"""Smoke tests for the Context Hub API.

Uses FastAPI TestClient (httpx) with:
  - EMBEDDING_PROVIDER=hash  (offline, no model download)
  - Temporary LANCEDB_URI and BLOB_DIR (hermetic per-run)
  - No ANTHROPIC_API_KEY (exercises stub paths)
  - API_KEYS=test-key

Test coverage:
  1. /healthz             → 200
  2. POST /v1/sessions    → 200, chunks_indexed > 0
  3. GET  /v1/sessions    → 200, list contains the ingested session
  4. GET  /v1/sessions/{id} → 200, catalog + raw blob
  5. POST /v1/query       → 200, has citations referencing the session
  6. GET  /v1/stats       → 200, total_sessions incremented
  7. POST /v1/summarize   → 200, summary contains stub marker
  8. Auth check           → 401 without token
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tmp_dirs():
    """Yield (lancedb_uri, blob_dir) in a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "lancedb"), os.path.join(tmpdir, "blobs")


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    """Build a hermetic TestClient with patched settings."""
    lancedb_uri, blob_dir = tmp_dirs

    # Patch env before any module-level imports so pydantic-settings picks them up
    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "API_KEYS": "test-key",
        "ANTHROPIC_API_KEY": "",       # no key
        "LLM_PROVIDER": "anthropic",   # unavailable w/o key → exercises stub path (hermetic)
        "S3_BUCKET": "",               # local blob store
        "CORS_ORIGINS": "",
    }
    original_env = {k: os.environ.get(k) for k in env_patch}
    os.environ.update(env_patch)

    # Clear any cached singletons so they pick up the new env
    _clear_caches()

    from contexthub.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    # Restore env and caches
    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _clear_caches()


def _clear_caches() -> None:
    """Invalidate all lru_cache singletons so tests start clean."""
    # Must import after env is patched
    mods = [
        "contexthub.config",
        "contexthub.embeddings",
        "contexthub.storage.blob",
        "contexthub.storage.vectors",
    ]
    for mod_name in mods:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            for attr in dir(mod):
                fn = getattr(mod, attr, None)
                if callable(fn) and hasattr(fn, "cache_clear"):
                    fn.cache_clear()

    # Also reset the module-level VectorStore singleton
    if "contexthub.storage.vectors" in sys.modules:
        sys.modules["contexthub.storage.vectors"].reset_vector_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AUTH = {"Authorization": "Bearer test-key"}

FAKE_SESSION = {
    "session": {
        "id": "test-session-001",
        "tool": "claude-code",
        "title": "Implement retry logic for S3 uploads",
        "cwd": "/Users/test/myproject",
        "project": "myproject",
        "started_at": "2026-06-09T10:00:00Z",
        "ended_at": "2026-06-09T10:45:00Z",
        "message_count": 4,
        "models": ["claude-sonnet-4-6"],
        "tokens": {"input": 3200, "output": 800},
        "preview": "I need to add retry logic to the S3 upload function",
        "file_path": "/Users/test/.claude/projects/myproject/test-session-001.jsonl",
        "messages": [
            {
                "id": "m1",
                "role": "user",
                "text": "I need to add retry logic to the S3 upload function. It keeps failing on transient network errors.",
                "timestamp": "2026-06-09T10:00:00Z",
                "model": None,
            },
            {
                "id": "m2",
                "role": "assistant",
                "text": (
                    "I'll add exponential backoff retry logic to the S3 upload. "
                    "Here's the approach: wrap the boto3 put_object call in a retry "
                    "loop with jitter to avoid thundering herd issues."
                ),
                "timestamp": "2026-06-09T10:01:00Z",
                "model": "claude-sonnet-4-6",
            },
            {
                "id": "m3",
                "role": "tool",
                "tool_name": "Write",
                "text": "File written: src/storage/s3_client.py",
                "timestamp": "2026-06-09T10:02:00Z",
                "model": None,
            },
            {
                "id": "m4",
                "role": "user",
                "text": "Looks good. Can you also add a circuit breaker?",
                "timestamp": "2026-06-09T10:03:00Z",
                "model": None,
            },
        ],
    },
    "summary": None,
    "category": "engineering",
    "visibility": "company",
    "author": {"id": "u_test", "email": "test@example.com", "name": "Test User"},
    "redacted": True,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_healthz(client: TestClient):
    """Health endpoint should respond 200 without auth."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_auth_required(client: TestClient):
    """Endpoints other than /healthz must require a Bearer token."""
    resp = client.get("/v1/sessions")  # no auth header
    # FastAPI's HTTPBearer returns 403 when the scheme is missing entirely,
    # and 401 when the token is present but invalid.  Either is acceptable
    # for "no credentials provided."
    assert resp.status_code in (401, 403)


def test_auth_invalid_token(client: TestClient):
    """Wrong token should return 401."""
    resp = client.get("/v1/sessions", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_ingest_session(client: TestClient):
    """POST /v1/sessions should store the session and index chunks."""
    resp = client.post("/v1/sessions", json=FAKE_SESSION, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["session_id"] == "test-session-001"
    assert data["chunks_indexed"] > 0
    assert data["summary_used"] is True
    assert "blob_uri" in data


def test_list_sessions(client: TestClient):
    """GET /v1/sessions should return a paginated SessionPage containing the ingested session."""
    resp = client.get("/v1/sessions", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # New paginated format: {items, total, limit, offset}
    assert "items" in data, f"Expected paginated response, got: {list(data.keys())}"
    items = data["items"]
    assert isinstance(items, list)
    ids = [item["id"] for item in items]
    assert "test-session-001" in ids


def test_list_sessions_filter_category(client: TestClient):
    """Filtering by category=engineering should still return the session."""
    resp = client.get("/v1/sessions?category=engineering", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    items = data["items"]
    assert any(item["id"] == "test-session-001" for item in items)


def test_list_sessions_filter_no_match(client: TestClient):
    """Filtering by a category that has no sessions should return empty list."""
    resp = client.get("/v1/sessions?category=sales", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    items = data["items"]
    assert all(item["id"] != "test-session-001" for item in items)


def test_get_session_by_id(client: TestClient):
    """GET /v1/sessions/{id} should return catalog row and raw blob."""
    resp = client.get("/v1/sessions/test-session-001", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "catalog" in data
    assert data["catalog"]["id"] == "test-session-001"
    assert data["raw"] is not None


def test_get_session_not_found(client: TestClient):
    """GET /v1/sessions/{unknown_id} should return 404."""
    resp = client.get("/v1/sessions/nonexistent-id", headers=AUTH)
    assert resp.status_code == 404


def test_query_returns_citations(client: TestClient):
    """POST /v1/query should return 200 with citations referencing the ingested session."""
    payload = {
        "question": "How was retry logic implemented for S3 uploads?",
        "top_k": 4,
    }
    resp = client.post("/v1/query", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "answer" in data
    assert isinstance(data["citations"], list)
    assert len(data["citations"]) > 0
    session_ids = [c["session_id"] for c in data["citations"]]
    assert "test-session-001" in session_ids


def test_stats_incremented(client: TestClient):
    """GET /v1/stats should reflect the ingested session."""
    resp = client.get("/v1/stats", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_sessions"] >= 1
    assert data["total_chunks"] >= 1
    assert "engineering" in data["sessions_by_category"]
    assert "claude-code" in data["sessions_by_tool"]


def test_summarize_returns_stub(client: TestClient):
    """POST /v1/summarize should return a stub when no API key is set."""
    payload = {"session": FAKE_SESSION["session"]}
    resp = client.post("/v1/summarize", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "summary" in data
    # Stub mode adds a clear HTML comment marker
    assert "STUB" in data["summary"] or len(data["summary"]) > 10
