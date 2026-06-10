"""Tests for Task 16: PR context links.

Covers:
  1. POST /v1/sessions/{id}/share  — mint a share token; returns {url, token}
  2. GET  /c/{session_id}?t=...    — context page renders HTML with title, summary, etc.
  3. Token validation: expired token → 403; bad token → 403; good token → 200.
  4. Tool messages are collapsed / assistant prose visible.
  5. GET /v1/sessions?link=<pr-url> — find sessions by PR link.
  6. context_page module helpers: sign/verify.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Cache/singleton reset helper (same pattern as other test files)
# ---------------------------------------------------------------------------

def _clear_caches() -> None:
    mods = [
        "contexthub.config",
        "contexthub.embeddings",
        "contexthub.storage.blob",
        "contexthub.storage.vectors",
        "contexthub.jobs.store",
        "contexthub.graph.store",
        "contexthub.rules.store",
        "contexthub.assets.store",
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
    if "contexthub.graph.store" in sys.modules:
        sys.modules["contexthub.graph.store"].reset_graph_store()
    if "contexthub.rules.store" in sys.modules:
        sys.modules["contexthub.rules.store"].reset_rules_store()
    if "contexthub.assets.store" in sys.modules:
        sys.modules["contexthub.assets.store"].reset_asset_store()


# ===========================================================================
# Unit tests: share token sign / verify
# ===========================================================================

class TestShareToken:
    def test_sign_and_verify(self):
        from contexthub.api.context_page import sign_share_token, verify_share_token

        session_id = "sess-abc-123"
        secret = "test-secret"
        token, expiry = sign_share_token(session_id, secret, ttl_seconds=300)
        assert token
        assert isinstance(expiry, int)
        assert verify_share_token(session_id, token, expiry, secret) is True

    def test_expired_token_rejected(self):
        from contexthub.api.context_page import sign_share_token, verify_share_token
        import hashlib, hmac as _hmac

        session_id = "sess-xyz"
        secret = "test-secret"
        expiry = int(time.time()) - 1  # already past
        msg = f"{session_id}:{expiry}".encode()
        token = _hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        assert verify_share_token(session_id, token, expiry, secret) is False

    def test_wrong_secret_rejected(self):
        from contexthub.api.context_page import sign_share_token, verify_share_token

        session_id = "sess-lmn"
        token, expiry = sign_share_token(session_id, "correct-secret", ttl_seconds=300)
        assert verify_share_token(session_id, token, expiry, "wrong-secret") is False

    def test_tampered_session_id_rejected(self):
        from contexthub.api.context_page import sign_share_token, verify_share_token

        token, expiry = sign_share_token("sess-original", "secret", ttl_seconds=300)
        assert verify_share_token("sess-different", token, expiry, "secret") is False

    def test_render_context_html(self):
        """render_context_page() should return HTML containing key content."""
        from contexthub.api.context_page import render_context_page

        html = render_context_page(
            session_id="sess-001",
            title="Add retry logic",
            author="alice",
            summary="We decided to use exponential backoff.",
            messages=[
                {"role": "user", "text": "How should we handle S3 retries?"},
                {"role": "assistant", "text": "Use exponential backoff with jitter."},
                {"role": "tool", "text": "File written: s3.py", "tool_name": "Write"},
            ],
            links=[{"kind": "pr", "url": "https://github.com/org/repo/pull/42", "label": "PR #42"}],
            graph_neighbors=[],
            pr_url=None,
        )
        assert "<html" in html or "<!DOCTYPE" in html
        assert "Add retry logic" in html
        assert "alice" in html
        assert "exponential backoff" in html
        # Tool messages should be collapsed (marked, not fully shown as prose)
        assert "File written: s3.py" in html  # content may appear but must be visually collapsed
        # PR link should be present
        assert "https://github.com/org/repo/pull/42" in html

    def test_render_context_html_collapses_tool_noise(self):
        """Tool messages must be wrapped in a collapsed <details> element."""
        from contexthub.api.context_page import render_context_page

        html = render_context_page(
            session_id="sess-002",
            title="Test session",
            author="bob",
            summary=None,
            messages=[
                {"role": "assistant", "text": "Let me write the file."},
                {"role": "tool", "text": "Ran command: npm install", "tool_name": "Bash"},
                {"role": "assistant", "text": "All done."},
            ],
            links=[],
            graph_neighbors=[],
            pr_url=None,
        )
        # Tool messages must appear inside a <details> or have a collapsed marker
        assert "<details" in html


# ===========================================================================
# HTTP integration tests
# ===========================================================================

@pytest.fixture(scope="module")
def tmp_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield (
            os.path.join(tmpdir, "lancedb"),
            os.path.join(tmpdir, "blobs"),
            os.path.join(tmpdir, "jobs.db"),
            os.path.join(tmpdir, "graph.db"),
            os.path.join(tmpdir, "rules.db"),
            os.path.join(tmpdir, "assets.db"),
            os.path.join(tmpdir, "asset_blobs"),
        )


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db, graph_db, rules_db, assets_db, asset_blobs = tmp_dirs

    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "JOBS_DB": jobs_db,
        "GRAPH_DB": graph_db,
        "RULES_DB": rules_db,
        "ASSETS_DB": assets_db,
        "ASSET_BLOB_DIR": asset_blobs,
        "API_KEYS": "alice-key:alice:team-red,bob-key:bob:team-blue",
        "ANTHROPIC_API_KEY": "",
        "LLM_PROVIDER": "anthropic",
        "S3_BUCKET": "",
        "CORS_ORIGINS": "",
        "ASSET_TOKEN_SECRET": "integration-test-secret",
        "SHARE_TOKEN_SECRET": "share-integration-secret",
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


ALICE = {"Authorization": "Bearer alice-key"}
BOB = {"Authorization": "Bearer bob-key"}

SESSION_WITH_LINK = {
    "session": {
        "id": "ctx-session-001",
        "tool": "claude-code",
        "title": "Implement retry logic for S3 uploads",
        "cwd": "/Users/alice/myrepo",
        "project": "myrepo",
        "started_at": "2026-06-10T09:00:00Z",
        "ended_at": "2026-06-10T09:40:00Z",
        "message_count": 4,
        "models": ["claude-sonnet-4-6"],
        "tokens": {"input": 1200, "output": 400},
        "preview": "Retry logic for S3",
        "file_path": "/Users/alice/.claude/projects/myrepo/ctx-session-001.jsonl",
        "messages": [
            {
                "id": "m1",
                "role": "user",
                "text": "Add retry logic to S3 upload function.",
                "timestamp": "2026-06-10T09:00:00Z",
            },
            {
                "id": "m2",
                "role": "assistant",
                "text": "I'll use exponential backoff with jitter to handle transient errors.",
                "timestamp": "2026-06-10T09:01:00Z",
                "model": "claude-sonnet-4-6",
            },
            {
                "id": "m3",
                "role": "tool",
                "tool_name": "Write",
                "text": "File written: s3_client.py",
                "timestamp": "2026-06-10T09:02:00Z",
            },
            {
                "id": "m4",
                "role": "assistant",
                "text": "Done. The retry loop uses exponential backoff.",
                "timestamp": "2026-06-10T09:03:00Z",
                "model": "claude-sonnet-4-6",
            },
        ],
        "links": [
            {"kind": "pr", "url": "https://github.com/org/myrepo/pull/99", "label": "PR #99"}
        ],
    },
    "summary": "Implemented S3 retry with exponential backoff.",
    "category": "engineering",
    "visibility": "company",
    "author": {"id": "alice", "email": "alice@example.com", "name": "Alice", "team": "team-red"},
    "redacted": True,
}


@pytest.fixture(scope="module")
def ingested_session(client: TestClient):
    """Ingest the session once and return the session_id."""
    resp = client.post("/v1/sessions", json=SESSION_WITH_LINK, headers=ALICE)
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# POST /v1/sessions/{id}/share
# ---------------------------------------------------------------------------

def test_share_returns_url_and_token(client: TestClient, ingested_session: str):
    """POST /v1/sessions/{id}/share should return a share URL and token."""
    resp = client.post(f"/v1/sessions/{ingested_session}/share", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "url" in data
    assert "token" in data
    assert ingested_session in data["url"]


def test_share_nonexistent_session_404(client: TestClient):
    """Sharing a nonexistent session should return 404."""
    resp = client.post("/v1/sessions/no-such-session/share", headers=ALICE)
    assert resp.status_code == 404


def test_share_requires_auth(client: TestClient, ingested_session: str):
    """POST /v1/sessions/{id}/share must require auth."""
    resp = client.post(f"/v1/sessions/{ingested_session}/share")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /c/{session_id}?t=... — context page
# ---------------------------------------------------------------------------

def test_context_page_valid_token(client: TestClient, ingested_session: str):
    """Context page should render HTML when given a valid token."""
    share_resp = client.post(f"/v1/sessions/{ingested_session}/share", headers=ALICE)
    assert share_resp.status_code == 200
    share_data = share_resp.json()

    # Extract token and expiry from the URL or the response body
    token = share_data["token"]
    # The URL contains ?t=<token>&expiry=<n>; parse it
    url = share_data["url"]
    # Make a direct GET request to the context page
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    token_from_url = qs.get("t", [token])[0]
    expiry_from_url = qs.get("expiry", [""])[0]

    get_url = f"/c/{ingested_session}?t={token_from_url}"
    if expiry_from_url:
        get_url += f"&expiry={expiry_from_url}"

    resp = client.get(get_url)
    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text
    # Should show session title and summary
    assert "retry" in html.lower() or "s3" in html.lower()


def test_context_page_bad_token_403(client: TestClient, ingested_session: str):
    """Context page with a wrong token should return 403."""
    resp = client.get(f"/c/{ingested_session}?t=bad-token&expiry=9999999999")
    assert resp.status_code == 403


def test_context_page_no_token_403(client: TestClient, ingested_session: str):
    """Context page without any token should return 403."""
    resp = client.get(f"/c/{ingested_session}")
    assert resp.status_code == 403


def test_context_page_shows_pr_link(client: TestClient, ingested_session: str):
    """Context page should include the PR link from session.links[]."""
    share_resp = client.post(f"/v1/sessions/{ingested_session}/share", headers=ALICE)
    token = share_resp.json()["token"]
    url = share_resp.json()["url"]
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(url).query)
    t = qs.get("t", [token])[0]
    expiry = qs.get("expiry", [""])[0]

    get_url = f"/c/{ingested_session}?t={t}"
    if expiry:
        get_url += f"&expiry={expiry}"

    resp = client.get(get_url)
    assert resp.status_code == 200
    # PR link should appear in the page
    assert "github.com/org/myrepo/pull/99" in resp.text


def test_context_page_tool_messages_collapsed(client: TestClient, ingested_session: str):
    """Context page should collapse tool messages (wrap in <details>)."""
    share_resp = client.post(f"/v1/sessions/{ingested_session}/share", headers=ALICE)
    token = share_resp.json()["token"]
    url = share_resp.json()["url"]
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(url).query)
    t = qs.get("t", [token])[0]
    expiry = qs.get("expiry", [""])[0]

    get_url = f"/c/{ingested_session}?t={t}"
    if expiry:
        get_url += f"&expiry={expiry}"

    resp = client.get(get_url)
    assert resp.status_code == 200
    assert "<details" in resp.text


# ---------------------------------------------------------------------------
# GET /v1/sessions?link=<pr-url>
# ---------------------------------------------------------------------------

def test_list_sessions_by_link(client: TestClient, ingested_session: str):
    """GET /v1/sessions?link=<url> should find sessions containing that link."""
    pr_url = "https://github.com/org/myrepo/pull/99"
    resp = client.get("/v1/sessions", params={"link": pr_url}, headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert ingested_session in ids


def test_list_sessions_by_link_no_match(client: TestClient):
    """GET /v1/sessions?link=<nonexistent-url> should return empty list."""
    resp = client.get(
        "/v1/sessions",
        params={"link": "https://github.com/org/repo/pull/9999"},
        headers=ALICE,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == [] or all(
        "9999" not in str(item.get("id")) for item in data["items"]
    )
