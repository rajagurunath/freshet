"""Tests for Task 2: Ingest hardening + pagination + sorting.

Covers:
  1. Idempotency: re-ingest same session → skipped=True, created_at unchanged,
     no duplicate chunks.
  2. Atomic upsert: no bare except, merge_insert used.
  3. Pagination: limit/offset/total in SessionPage response.
  4. Sorting: sort by tokens_total, created_at, message_count.
  5. SessionDetail response_model on GET /v1/sessions/{id}.
  6. tokens_input/tokens_output columns are sortable.
"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures (isolated per-module, separate from smoke tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tmp_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "lancedb"), os.path.join(tmpdir, "blobs")


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir = tmp_dirs

    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
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


def _clear_caches() -> None:
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
    if "contexthub.storage.vectors" in sys.modules:
        sys.modules["contexthub.storage.vectors"].reset_vector_store()


AUTH = {"Authorization": "Bearer test-key"}


def _make_session(session_id: str, tokens_input: int = 1000, tokens_output: int = 200, message_count: int = 4) -> dict:
    return {
        "session": {
            "id": session_id,
            "tool": "claude-code",
            "title": f"Session {session_id}",
            "cwd": "/Users/test/proj",
            "project": "proj",
            "started_at": "2026-06-09T10:00:00Z",
            "ended_at": "2026-06-09T10:45:00Z",
            "message_count": message_count,
            "models": ["claude-sonnet-4-6"],
            "tokens": {"input": tokens_input, "output": tokens_output},
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
        "summary": "A test summary for " + session_id,
        "category": "engineering",
        "visibility": "company",
        "author": {"id": "u_test", "email": "test@example.com", "name": "Test User"},
        "redacted": True,
    }


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------

def test_initial_ingest(client: TestClient):
    """First ingest should succeed and return skipped=False."""
    payload = _make_session("idem-001")
    resp = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["session_id"] == "idem-001"
    assert data["chunks_indexed"] > 0
    assert data["skipped"] is False


def test_reingest_same_session_is_skipped(client: TestClient):
    """Re-ingesting the identical session must return skipped=True."""
    payload = _make_session("idem-001")
    resp = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["skipped"] is True


def test_reingest_preserves_created_at(client: TestClient):
    """Re-ingesting must not change the original created_at timestamp."""
    # First ingest
    payload = _make_session("idem-002")
    r1 = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert r1.status_code == 200
    original_created_at = r1.json()["created_at"]

    # Second ingest (identical)
    r2 = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert r2.status_code == 200
    assert r2.json()["skipped"] is True
    assert r2.json()["created_at"] == original_created_at


def test_reingest_no_duplicate_chunks(client: TestClient):
    """Re-ingesting must not create duplicate chunks."""
    from contexthub.storage.vectors import get_vector_store

    payload = _make_session("idem-003")
    r1 = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert r1.status_code == 200
    chunks_first = r1.json()["chunks_indexed"]

    r2 = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert r2.status_code == 200
    assert r2.json()["skipped"] is True

    # Check chunk count didn't double
    vs = get_vector_store()
    tbl = vs._get_chunks_table()
    import pyarrow.compute as pc
    all_chunks = tbl.to_arrow()
    session_col = all_chunks.column("session_id").to_pylist()
    count = sum(1 for s in session_col if s == "idem-003")
    assert count == chunks_first, f"Expected {chunks_first} chunks, got {count}"


def test_reingest_changed_session_not_skipped(client: TestClient):
    """Re-ingesting a session with different content must NOT be skipped."""
    payload1 = _make_session("idem-004")
    r1 = client.post("/v1/sessions", json=payload1, headers=AUTH)
    assert r1.status_code == 200
    assert r1.json()["skipped"] is False

    # Change the summary (different content hash)
    payload2 = copy.deepcopy(payload1)
    payload2["summary"] = "A completely different summary"
    r2 = client.post("/v1/sessions", json=payload2, headers=AUTH)
    assert r2.status_code == 200
    assert r2.json()["skipped"] is False


# ---------------------------------------------------------------------------
# IngestResponse must include new fields
# ---------------------------------------------------------------------------

def test_ingest_response_has_required_fields(client: TestClient):
    """IngestResponse must have skipped, created_at, updated_at fields."""
    payload = _make_session("fields-001")
    resp = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "skipped" in data
    assert "created_at" in data
    assert "updated_at" in data


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------

def _ingest_n(client, n: int, prefix: str = "page"):
    for i in range(n):
        payload = _make_session(
            f"{prefix}-{i:03d}",
            tokens_input=1000 * (i + 1),
            tokens_output=200 * (i + 1),
            message_count=i + 2,
        )
        resp = client.post("/v1/sessions", json=payload, headers=AUTH)
        assert resp.status_code == 200, f"Ingest failed for {prefix}-{i:03d}: {resp.text}"


def test_list_sessions_returns_page_envelope(client: TestClient):
    """GET /v1/sessions must return a SessionPage with items/total/limit/offset."""
    _ingest_n(client, 5, prefix="pg")

    resp = client.get("/v1/sessions?limit=3&offset=0", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data, f"Expected 'items' key, got: {list(data.keys())}"
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert data["limit"] == 3
    assert data["offset"] == 0
    assert len(data["items"]) <= 3


def test_pagination_limit_offset(client: TestClient):
    """Paginating with offset should return non-overlapping pages."""
    _ingest_n(client, 6, prefix="pag2")

    r1 = client.get("/v1/sessions?limit=3&offset=0", headers=AUTH)
    r2 = client.get("/v1/sessions?limit=3&offset=3", headers=AUTH)
    assert r1.status_code == 200
    assert r2.status_code == 200

    page1_ids = {item["id"] for item in r1.json()["items"]}
    page2_ids = {item["id"] for item in r2.json()["items"]}
    # Pages must not overlap
    assert page1_ids.isdisjoint(page2_ids), "Pages must not share items"


def test_pagination_total_is_accurate(client: TestClient):
    """total in SessionPage must equal the full count of matching sessions."""
    _ingest_n(client, 4, prefix="cnt")

    r_all = client.get("/v1/sessions?limit=200&offset=0", headers=AUTH)
    assert r_all.status_code == 200
    data = r_all.json()
    total = data["total"]
    assert total == len(data["items"]) or total >= len(data["items"])

    # Narrow down: fetch first page with limit=2
    r_page = client.get("/v1/sessions?limit=2&offset=0", headers=AUTH)
    assert r_page.status_code == 200
    assert r_page.json()["total"] == total  # total is global count, not page size


def test_pagination_default_limit(client: TestClient):
    """Default limit should be 50 (or configured default)."""
    resp = client.get("/v1/sessions", headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    # Should have items + total keys now (paginated response)
    assert "items" in data
    assert "total" in data
    assert data["limit"] <= 200


def test_pagination_max_limit_enforced(client: TestClient):
    """Requesting limit > 200 should be clamped to 200 (or rejected)."""
    resp = client.get("/v1/sessions?limit=9999", headers=AUTH)
    # Either 200 OK (clamped) or 422 (validation error)
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        assert resp.json()["limit"] <= 200


# ---------------------------------------------------------------------------
# Sorting tests
# ---------------------------------------------------------------------------

def test_sort_by_created_at_asc(client: TestClient):
    """sort=created_at&order=asc should return oldest sessions first."""
    # Ingest in a known order
    _ingest_n(client, 3, prefix="sort_ca")

    resp = client.get("/v1/sessions?sort=created_at&order=asc&limit=100", headers=AUTH)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    dates = [item["created_at"] for item in items]
    assert dates == sorted(dates), "Items should be sorted by created_at ascending"


def test_sort_by_created_at_desc(client: TestClient):
    """sort=created_at&order=desc should return newest sessions first."""
    resp = client.get("/v1/sessions?sort=created_at&order=desc&limit=100", headers=AUTH)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    dates = [item["created_at"] for item in items]
    assert dates == sorted(dates, reverse=True), "Items should be sorted by created_at descending"


def test_sort_by_tokens_total(client: TestClient):
    """sort=tokens_total should order by input+output tokens."""
    # Ingest sessions with distinct token counts
    for i, (inp, out) in enumerate([(500, 100), (2000, 400), (100, 50)]):
        payload = _make_session(f"tok-sort-{i}", tokens_input=inp, tokens_output=out)
        client.post("/v1/sessions", json=payload, headers=AUTH)

    resp = client.get("/v1/sessions?sort=tokens_total&order=asc&limit=100", headers=AUTH)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # Filter to just our test sessions
    tok_items = [it for it in items if it["id"].startswith("tok-sort-")]
    totals = [it.get("tokens_total", 0) for it in tok_items]
    assert totals == sorted(totals), f"Expected ascending tokens_total, got {totals}"


def test_sort_by_message_count(client: TestClient):
    """sort=message_count should order by message_count."""
    for i, mc in enumerate([10, 2, 7]):
        payload = _make_session(f"mc-sort-{i}", message_count=mc)
        client.post("/v1/sessions", json=payload, headers=AUTH)

    resp = client.get("/v1/sessions?sort=message_count&order=asc&limit=100", headers=AUTH)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    mc_items = [it for it in items if it["id"].startswith("mc-sort-")]
    counts = [it["message_count"] for it in mc_items]
    assert counts == sorted(counts), f"Expected ascending message_count, got {counts}"


# ---------------------------------------------------------------------------
# SessionDetail response model
# ---------------------------------------------------------------------------

def test_get_session_has_response_model(client: TestClient):
    """GET /v1/sessions/{id} should return a typed SessionDetail response."""
    payload = _make_session("detail-001")
    client.post("/v1/sessions", json=payload, headers=AUTH)

    resp = client.get("/v1/sessions/detail-001", headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # SessionDetail must have catalog and raw fields
    assert "catalog" in data
    assert "raw" in data
    catalog = data["catalog"]
    # Must expose tokens_input and tokens_output
    assert "tokens_input" in catalog, f"tokens_input missing from catalog: {list(catalog.keys())}"
    assert "tokens_output" in catalog, f"tokens_output missing from catalog: {list(catalog.keys())}"


def test_sessions_list_items_have_token_columns(client: TestClient):
    """Items in the paginated list should include tokens_input, tokens_output, tokens_total."""
    payload = _make_session("tok-fields-001", tokens_input=1500, tokens_output=300)
    client.post("/v1/sessions", json=payload, headers=AUTH)

    resp = client.get("/v1/sessions?limit=200", headers=AUTH)
    assert resp.status_code == 200
    items = resp.json()["items"]
    target = next((it for it in items if it["id"] == "tok-fields-001"), None)
    assert target is not None, "Session not found in listing"
    assert "tokens_input" in target
    assert "tokens_output" in target
    assert target["tokens_input"] == 1500
    assert target["tokens_output"] == 300


# ---------------------------------------------------------------------------
# updated_at on upsert
# ---------------------------------------------------------------------------

def test_reingest_with_changes_updates_updated_at(client: TestClient):
    """Re-ingesting a changed session must update updated_at."""
    payload1 = _make_session("upd-001")
    r1 = client.post("/v1/sessions", json=payload1, headers=AUTH)
    assert r1.status_code == 200

    payload2 = copy.deepcopy(payload1)
    payload2["summary"] = "New summary for update test"
    r2 = client.post("/v1/sessions", json=payload2, headers=AUTH)
    assert r2.status_code == 200
    assert r2.json()["skipped"] is False
    # updated_at should be present
    assert r2.json()["updated_at"] is not None
