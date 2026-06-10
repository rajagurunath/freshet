"""Tests for Task 4: Identity + visibility enforcement.

Covers:
  1. API_KEYS format: key:user_id:team triples (and bare-key backward compat).
  2. require_api_key returns a Caller with user_id and team.
  3. Private sessions are invisible to other callers in list, get, and query.
  4. Team-scoped sessions are visible only to callers on the same team.
  5. Company-wide sessions are visible to all authenticated callers.
  6. Visibility enforced in all read paths: list, get (404), and query citations.
"""

from __future__ import annotations

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
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "lancedb"), os.path.join(tmpdir, "blobs")


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir = tmp_dirs

    # Three API keys:
    #   alice-key  → alice  / team-red
    #   bob-key    → bob    / team-blue
    #   anon-key   → bare key (anonymous, backward compat)
    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "API_KEYS": "alice-key:alice:team-red,bob-key:bob:team-blue,anon-key",
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


ALICE = {"Authorization": "Bearer alice-key"}
BOB = {"Authorization": "Bearer bob-key"}
ANON = {"Authorization": "Bearer anon-key"}


def _make_session(
    session_id: str,
    text: str = "test content",
    visibility: str = "company",
    author_id: str = "alice",
    team: str | None = "team-red",
    title: str | None = None,
) -> dict:
    session = {
        "session": {
            "id": session_id,
            "tool": "claude-code",
            "title": title or f"Session {session_id}",
            "cwd": "/Users/test/proj",
            "project": "proj",
            "started_at": "2026-06-09T10:00:00Z",
            "ended_at": "2026-06-09T10:45:00Z",
            "message_count": 2,
            "models": ["claude-sonnet-4-6"],
            "tokens": {"input": 100, "output": 50},
            "preview": text[:80],
            "file_path": f"/Users/test/.claude/projects/proj/{session_id}.jsonl",
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "text": text,
                    "timestamp": "2026-06-09T10:00:00Z",
                    "model": None,
                },
            ],
        },
        "summary": f"Summary for {session_id}",
        "category": "engineering",
        "visibility": visibility,
        "author": {"id": author_id, "email": f"{author_id}@example.com", "name": author_id.title()},
        "redacted": True,
    }
    if team is not None:
        session["author"]["team"] = team
    return session


# ---------------------------------------------------------------------------
# Unit tests: Caller model + API_KEYS parsing
# ---------------------------------------------------------------------------

class TestCallerParsing:
    """config.api_key_triples parses key:user_id:team triples correctly."""

    def test_triple_parsed(self):
        from contexthub.config import Settings

        s = Settings(api_keys="mykey:alice:team-red", lancedb_uri="/tmp/x", blob_dir="/tmp/b")
        triples = s.api_key_triples
        assert len(triples) == 1
        key, user_id, team = triples[0]
        assert key == "mykey"
        assert user_id == "alice"
        assert team == "team-red"

    def test_bare_key_backward_compat(self):
        from contexthub.config import Settings

        s = Settings(api_keys="barekey", lancedb_uri="/tmp/x", blob_dir="/tmp/b")
        triples = s.api_key_triples
        assert len(triples) == 1
        key, user_id, team = triples[0]
        assert key == "barekey"
        assert user_id is None
        assert team is None

    def test_mixed_keys(self):
        from contexthub.config import Settings

        s = Settings(
            api_keys="k1:u1:t1,k2:u2:t2,k3",
            lancedb_uri="/tmp/x",
            blob_dir="/tmp/b",
        )
        triples = s.api_key_triples
        assert len(triples) == 3
        assert triples[0] == ("k1", "u1", "t1")
        assert triples[1] == ("k2", "u2", "t2")
        key, user_id, team = triples[2]
        assert key == "k3"
        assert user_id is None
        assert team is None

    def test_api_key_list_unchanged(self):
        """api_key_list must still return only the bare keys (backward compat)."""
        from contexthub.config import Settings

        s = Settings(
            api_keys="k1:u1:t1,k2",
            lancedb_uri="/tmp/x",
            blob_dir="/tmp/b",
        )
        assert set(s.api_key_list) == {"k1", "k2"}

    def test_invalid_key_rejected(self):
        from contexthub.config import Settings
        from fastapi import HTTPException
        from contexthub.deps import _resolve_caller

        s = Settings(api_keys="goodkey:alice:team-red", lancedb_uri="/tmp/x", blob_dir="/tmp/b")
        with pytest.raises(HTTPException) as exc_info:
            _resolve_caller("badkey", s)
        assert exc_info.value.status_code == 401


class TestCallerModel:
    """Caller dataclass carries user_id and team."""

    def test_caller_attributes(self):
        from contexthub.deps import Caller

        c = Caller(user_id="alice", team="team-red")
        assert c.user_id == "alice"
        assert c.team == "team-red"

    def test_caller_none_fields(self):
        from contexthub.deps import Caller

        c = Caller(user_id=None, team=None)
        assert c.user_id is None
        assert c.team is None


# ---------------------------------------------------------------------------
# Integration tests: visibility enforcement via HTTP
# ---------------------------------------------------------------------------

class TestVisibilityEnforcement:
    """End-to-end visibility tests using the TestClient."""

    def test_company_session_visible_to_all(self, client: TestClient):
        """A company-wide session is visible to any valid caller."""
        payload = _make_session(
            "vis-company-001",
            text="company session content",
            visibility="company",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        # Both alice and bob can list it
        r_alice = client.get("/v1/sessions", headers=ALICE)
        r_bob = client.get("/v1/sessions", headers=BOB)
        assert r_alice.status_code == 200
        assert r_bob.status_code == 200
        alice_ids = {item["id"] for item in r_alice.json()["items"]}
        bob_ids = {item["id"] for item in r_bob.json()["items"]}
        assert "vis-company-001" in alice_ids
        assert "vis-company-001" in bob_ids

    def test_private_session_invisible_to_other_caller_in_list(self, client: TestClient):
        """A private session must not appear in another caller's list results."""
        payload = _make_session(
            "vis-private-001",
            text="alice private session content",
            visibility="private",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        # Bob must NOT see alice's private session
        r_bob = client.get("/v1/sessions", headers=BOB)
        assert r_bob.status_code == 200
        bob_ids = {item["id"] for item in r_bob.json()["items"]}
        assert "vis-private-001" not in bob_ids, (
            "Private session must not appear in another user's list"
        )

    def test_private_session_visible_to_owner(self, client: TestClient):
        """A private session must be visible to its owner."""
        payload = _make_session(
            "vis-private-002",
            text="alice private session 2",
            visibility="private",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        r_alice = client.get("/v1/sessions", headers=ALICE)
        alice_ids = {item["id"] for item in r_alice.json()["items"]}
        assert "vis-private-002" in alice_ids, "Owner must see their own private session"

    def test_private_session_get_returns_404_for_other_caller(self, client: TestClient):
        """GET /v1/sessions/{id} for a private session must return 404 to non-owners."""
        payload = _make_session(
            "vis-private-003",
            text="alice private session 3",
            visibility="private",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        # Bob tries to fetch it → must get 404
        r_bob = client.get("/v1/sessions/vis-private-003", headers=BOB)
        assert r_bob.status_code == 404, (
            f"Expected 404 for private session from non-owner, got {r_bob.status_code}"
        )

    def test_private_session_get_accessible_to_owner(self, client: TestClient):
        """GET /v1/sessions/{id} for a private session must succeed for the owner."""
        payload = _make_session(
            "vis-private-004",
            text="alice private session 4",
            visibility="private",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        r_alice = client.get("/v1/sessions/vis-private-004", headers=ALICE)
        assert r_alice.status_code == 200, "Owner must be able to GET their own private session"

    def test_team_session_visible_to_same_team(self, client: TestClient):
        """A team-scoped session must be visible to callers on the same team."""
        payload = _make_session(
            "vis-team-001",
            text="team red session content",
            visibility="team",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        # Alice is team-red — must see it
        r_alice = client.get("/v1/sessions", headers=ALICE)
        assert r_alice.status_code == 200
        alice_ids = {item["id"] for item in r_alice.json()["items"]}
        assert "vis-team-001" in alice_ids

    def test_team_session_invisible_to_other_team(self, client: TestClient):
        """A team-scoped session must be invisible to callers on a different team."""
        payload = _make_session(
            "vis-team-002",
            text="team red session content 2",
            visibility="team",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        # Bob is team-blue — must NOT see it
        r_bob = client.get("/v1/sessions", headers=BOB)
        bob_ids = {item["id"] for item in r_bob.json()["items"]}
        assert "vis-team-002" not in bob_ids, (
            "Team-scoped session must not appear to callers on a different team"
        )

    def test_team_session_get_404_for_other_team(self, client: TestClient):
        """GET /v1/sessions/{id} for a team session must 404 for other-team callers."""
        payload = _make_session(
            "vis-team-003",
            text="team red session content 3",
            visibility="team",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        r_bob = client.get("/v1/sessions/vis-team-003", headers=BOB)
        assert r_bob.status_code == 404, (
            f"Expected 404 for team-scoped session from different team, got {r_bob.status_code}"
        )

    def test_private_session_not_in_query_citations_for_other_caller(self, client: TestClient):
        """Private sessions must not appear in query citations returned to other callers."""
        unique_token = "ALICE_PRIVATE_UNIQUE_XR99"
        payload = _make_session(
            "vis-qprivate-001",
            text=f"implementation of {unique_token} feature for Alice",
            visibility="private",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        # Bob queries for the unique token — must NOT see alice's private session
        q = {"question": unique_token, "top_k": 10}
        r_bob = client.post("/v1/query", json=q, headers=BOB)
        assert r_bob.status_code == 200
        cited_ids = {c["session_id"] for c in r_bob.json()["citations"]}
        assert "vis-qprivate-001" not in cited_ids, (
            "Private session must not appear in query citations for another caller"
        )

    def test_anon_key_sees_company_sessions(self, client: TestClient):
        """Backward-compat bare key (anonymous) must still access company sessions."""
        payload = _make_session(
            "vis-anon-001",
            text="company session for anon test",
            visibility="company",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        r_anon = client.get("/v1/sessions", headers=ANON)
        assert r_anon.status_code == 200
        anon_ids = {item["id"] for item in r_anon.json()["items"]}
        assert "vis-anon-001" in anon_ids

    def test_anon_key_cannot_see_private_sessions(self, client: TestClient):
        """Anonymous callers (bare key) must not see private sessions."""
        payload = _make_session(
            "vis-anon-private-001",
            text="alice private for anon test",
            visibility="private",
            author_id="alice",
            team="team-red",
        )
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text

        r_anon = client.get("/v1/sessions", headers=ANON)
        assert r_anon.status_code == 200
        anon_ids = {item["id"] for item in r_anon.json()["items"]}
        assert "vis-anon-private-001" not in anon_ids, (
            "Anonymous caller must not see private sessions"
        )
