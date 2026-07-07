"""Tests for the PR-merge-style review workflow.

Covers:
  1. ReviewStore: create/reset requests, vote upsert (re-vote replaces), stats.
  2. Push with REVIEW_REQUIRED=true → review_status 'pending', nothing indexed
     (invisible in catalog + query) and no jobs enqueued.
  3. Author self-vote → 403; anonymous (bare-key) vote → 403.
  4. Approve by a non-author → 'approved' → deferred integration runs
     (catalog + query visibility, graph_extract job enqueued).
  5. Any reject vote → 'rejected' (blob retained, never indexed).
  6. Private sessions bypass review even when REVIEW_REQUIRED=true.
  7. Voting on a decided review → 409.
  8. approvals_required=2 needs two distinct reviewers.
  9. Default REVIEW_REQUIRED=false keeps legacy behavior (immediate integration).
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Cache reset helper (same pattern as test_rules.py)
# ---------------------------------------------------------------------------

def _clear_caches() -> None:
    mods = [
        "contexthub.config",
        "contexthub.embeddings",
        "contexthub.storage.blob",
        "contexthub.storage.vectors",
        "contexthub.jobs.store",
        "contexthub.graph.store",
        "contexthub.reviews.store",
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
    if "contexthub.reviews.store" in sys.modules:
        sys.modules["contexthub.reviews.store"].reset_review_store()


@contextlib.contextmanager
def _client_with_env(extra_env: dict[str, str]) -> Generator[TestClient, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        env_patch = {
            "EMBEDDING_PROVIDER": "hash",
            "LANCEDB_URI": os.path.join(tmpdir, "lancedb"),
            "BLOB_DIR": os.path.join(tmpdir, "blobs"),
            "JOBS_DB": os.path.join(tmpdir, "jobs.db"),
            "GRAPH_DB": os.path.join(tmpdir, "graph.db"),
            "REVIEWS_DB": os.path.join(tmpdir, "reviews.db"),
            "API_KEYS": "alice-key:alice:team-red,bob-key:bob:team-blue,carol-key:carol:team-red,anon-key",
            "ANTHROPIC_API_KEY": "",
            "LLM_PROVIDER": "anthropic",
            "S3_BUCKET": "",
            "CORS_ORIGINS": "",
            **extra_env,
        }
        original_env = {k: os.environ.get(k) for k in env_patch}
        os.environ.update(env_patch)

        _clear_caches()

        from contexthub.main import create_app

        app = create_app()
        try:
            with TestClient(app, raise_server_exceptions=True) as c:
                yield c
        finally:
            for k, v in original_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _clear_caches()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    """Client with REVIEW_REQUIRED=true and 1 approval required."""
    with _client_with_env({"REVIEW_REQUIRED": "true"}) as c:
        yield c


ALICE = {"Authorization": "Bearer alice-key"}
BOB = {"Authorization": "Bearer bob-key"}
CAROL = {"Authorization": "Bearer carol-key"}
ANON = {"Authorization": "Bearer anon-key"}


def _make_session(
    session_id: str,
    text: str = "test content",
    visibility: str = "company",
    author_id: str = "alice",
    team: str | None = "team-red",
) -> dict:
    return {
        "session": {
            "id": session_id,
            "tool": "claude-code",
            "title": f"Session {session_id}",
            "cwd": "/Users/test/proj",
            "project": "proj",
            "started_at": "2026-07-01T10:00:00Z",
            "ended_at": "2026-07-01T10:45:00Z",
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
                    "timestamp": "2026-07-01T10:00:00Z",
                    "model": None,
                },
                {
                    "id": "m2",
                    "role": "assistant",
                    "text": "Assistant reply for " + session_id,
                    "timestamp": "2026-07-01T10:01:00Z",
                    "model": "claude-sonnet-4-6",
                },
            ],
        },
        "summary": f"Summary for {session_id}",
        "category": "engineering",
        "visibility": visibility,
        "author": {"id": author_id, "email": f"{author_id}@example.com", "name": author_id.title(), "team": team},
        "redacted": True,
    }


def _catalog_ids(client: TestClient, headers: dict) -> set[str]:
    resp = client.get("/v1/sessions", headers=headers)
    assert resp.status_code == 200
    return {item["id"] for item in resp.json()["items"]}


def _cited_ids(client: TestClient, question: str, headers: dict) -> set[str]:
    resp = client.post("/v1/query", json={"question": question, "top_k": 10}, headers=headers)
    assert resp.status_code == 200
    return {c["session_id"] for c in resp.json()["citations"]}


# ===========================================================================
# Unit tests: ReviewStore (no HTTP)
# ===========================================================================

class TestReviewStore:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        from contexthub.reviews.store import ReviewStore
        self.store = ReviewStore(os.path.join(self._tmpdir.name, "reviews.db"))

    def teardown_method(self):
        self._tmpdir.cleanup()

    def _create(self, session_id: str = "s1", approvals: int = 1) -> None:
        self.store.create_request(
            session_id=session_id,
            author_id="alice",
            author_name="Alice",
            title="Test session",
            category="engineering",
            visibility="company",
            summary="A summary",
            approvals_required=approvals,
        )

    def test_create_and_get(self):
        self._create()
        row = self.store.get_request("s1")
        assert row is not None
        assert row["status"] == "pending"
        assert row["approvals_required"] == 1
        assert row["author_id"] == "alice"

    def test_revote_replaces_prior_vote(self):
        self._create()
        self.store.add_vote("s1", "bob", None, "approve")
        self.store.add_vote("s1", "bob", None, "reject", comment="changed my mind")
        votes = self.store.votes_for("s1")
        assert len(votes) == 1
        assert votes[0]["verdict"] == "reject"
        assert votes[0]["comment"] == "changed my mind"

    def test_repush_resets_status_and_votes(self):
        self._create()
        self.store.add_vote("s1", "bob", None, "reject")
        self.store.set_status("s1", "rejected")
        self._create()  # re-push
        row = self.store.get_request("s1")
        assert row["status"] == "pending"
        assert row["decided_at"] is None
        assert self.store.votes_for("s1") == []

    def test_stats(self):
        self._create("s1")
        self._create("s2")
        self.store.set_status("s2", "approved")
        self._create("s3")
        self.store.set_status("s3", "rejected")
        assert self.store.stats() == {"pending": 1, "approved": 1, "rejected": 1}

    def test_invalid_verdict_rejected(self):
        self._create()
        with pytest.raises(ValueError):
            self.store.add_vote("s1", "bob", None, "maybe")


# ===========================================================================
# Integration tests: review workflow via HTTP (approvals_required=1)
# ===========================================================================

class TestReviewWorkflow:
    def test_push_held_for_review(self, client: TestClient):
        """Push with review required → pending, unindexed, unqueryable."""
        token = "REVIEW_PENDING_TOKEN_AA11"
        payload = _make_session("rev-001", text=f"how we fixed {token} in prod")
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["review_status"] == "pending"
        assert data["chunks_indexed"] == 0
        assert data["blob_uri"]

        # Not in the catalog, not citable — for anyone.
        assert "rev-001" not in _catalog_ids(client, ALICE)
        assert "rev-001" not in _catalog_ids(client, BOB)
        assert "rev-001" not in _cited_ids(client, token, BOB)

        # No summarize/graph jobs enqueued for it yet.
        jobs = client.get("/v1/jobs", params={"kind": "graph_extract"}, headers=ALICE).json()
        assert not any(j["payload"].get("session_id") == "rev-001" for j in jobs)

        # It appears in the pending queue.
        reviews = client.get("/v1/reviews", headers=BOB).json()
        ids = {r["session_id"] for r in reviews["items"]}
        assert "rev-001" in ids

    def test_review_detail_includes_transcript(self, client: TestClient):
        resp = client.get("/v1/reviews/rev-001", headers=BOB)
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["review"]["session_id"] == "rev-001"
        assert detail["review"]["summary"] == "Summary for rev-001"
        assert len(detail["messages"]) == 2
        assert detail["messages"][0]["role"] == "user"

    def test_author_cannot_vote_own_session(self, client: TestClient):
        resp = client.post(
            "/v1/reviews/rev-001/vote", json={"verdict": "approve"}, headers=ALICE
        )
        assert resp.status_code == 403

    def test_anonymous_caller_cannot_vote(self, client: TestClient):
        resp = client.post(
            "/v1/reviews/rev-001/vote", json={"verdict": "approve"}, headers=ANON
        )
        assert resp.status_code == 403

    def test_approve_integrates_session(self, client: TestClient):
        """A non-author approve vote integrates the session into the brain."""
        token = "REVIEW_PENDING_TOKEN_AA11"
        resp = client.post(
            "/v1/reviews/rev-001/vote",
            json={"verdict": "approve", "comment": "LGTM"},
            headers=BOB,
        )
        assert resp.status_code == 200, resp.text
        review = resp.json()
        assert review["status"] == "approved"
        assert review["approve_count"] == 1
        assert review["my_vote"] == "approve"
        assert review["decided_at"]

        # Now indexed: catalog + query see it.
        assert "rev-001" in _catalog_ids(client, BOB)
        assert "rev-001" in _cited_ids(client, token, BOB)

        # Deferred jobs were enqueued on approval.
        jobs = client.get("/v1/jobs", params={"kind": "graph_extract"}, headers=ALICE).json()
        assert any(j["payload"].get("session_id") == "rev-001" for j in jobs)

    def test_vote_on_decided_review_conflicts(self, client: TestClient):
        resp = client.post(
            "/v1/reviews/rev-001/vote", json={"verdict": "reject"}, headers=CAROL
        )
        assert resp.status_code == 409

    def test_reject_keeps_session_out(self, client: TestClient):
        token = "REVIEW_REJECT_TOKEN_BB22"
        payload = _make_session("rev-002", text=f"secret {token} experiment")
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "pending"

        resp = client.post(
            "/v1/reviews/rev-002/vote",
            json={"verdict": "reject", "comment": "not suitable for the hub"},
            headers=BOB,
        )
        assert resp.status_code == 200, resp.text
        review = resp.json()
        assert review["status"] == "rejected"
        assert review["reject_count"] == 1

        # Never indexed.
        assert "rev-002" not in _catalog_ids(client, BOB)
        assert "rev-002" not in _cited_ids(client, token, BOB)

        # Blob retained: the review detail can still show the transcript.
        detail = client.get("/v1/reviews/rev-002", headers=BOB).json()
        assert len(detail["messages"]) == 2

        # Listed under the rejected filter.
        rejected = client.get("/v1/reviews", params={"status": "rejected"}, headers=BOB).json()
        assert "rev-002" in {r["session_id"] for r in rejected["items"]}

    def test_private_session_bypasses_review(self, client: TestClient):
        payload = _make_session("rev-priv-001", text="my private notes", visibility="private")
        resp = client.post("/v1/sessions", json=payload, headers=ALICE)
        assert resp.status_code == 200
        data = resp.json()
        assert data["review_status"] is None
        assert data["chunks_indexed"] > 0
        assert "rev-priv-001" in _catalog_ids(client, ALICE)

    def test_stats_endpoint(self, client: TestClient):
        resp = client.get("/v1/reviews/stats", headers=ALICE)
        assert resp.status_code == 200
        stats = resp.json()
        assert stats["approved"] >= 1
        assert stats["rejected"] >= 1

    def test_invalid_status_filter(self, client: TestClient):
        resp = client.get("/v1/reviews", params={"status": "bogus"}, headers=ALICE)
        assert resp.status_code == 422

    def test_review_not_found(self, client: TestClient):
        assert client.get("/v1/reviews/nope-000", headers=ALICE).status_code == 404
        assert (
            client.post(
                "/v1/reviews/nope-000/vote", json={"verdict": "approve"}, headers=BOB
            ).status_code
            == 404
        )


# ===========================================================================
# approvals_required=2 needs two distinct reviewers
# ===========================================================================

class TestTwoApprovals:
    def test_two_distinct_reviewers_required(self):
        with _client_with_env(
            {"REVIEW_REQUIRED": "true", "REVIEW_APPROVALS_REQUIRED": "2"}
        ) as client:
            payload = _make_session("rev2-001", text="two approvals needed here")
            resp = client.post("/v1/sessions", json=payload, headers=ALICE)
            assert resp.status_code == 200
            assert resp.json()["review_status"] == "pending"

            # First approval: still pending, still unindexed.
            resp = client.post(
                "/v1/reviews/rev2-001/vote", json={"verdict": "approve"}, headers=BOB
            )
            assert resp.status_code == 200
            review = resp.json()
            assert review["status"] == "pending"
            assert review["approve_count"] == 1
            assert review["approvals_required"] == 2
            assert "rev2-001" not in _catalog_ids(client, BOB)

            # Same reviewer voting again does not double-count.
            resp = client.post(
                "/v1/reviews/rev2-001/vote", json={"verdict": "approve"}, headers=BOB
            )
            assert resp.status_code == 200
            assert resp.json()["approve_count"] == 1
            assert resp.json()["status"] == "pending"

            # Second distinct reviewer tips it over.
            resp = client.post(
                "/v1/reviews/rev2-001/vote", json={"verdict": "approve"}, headers=CAROL
            )
            assert resp.status_code == 200
            review = resp.json()
            assert review["status"] == "approved"
            assert review["approve_count"] == 2
            assert "rev2-001" in _catalog_ids(client, BOB)


# ===========================================================================
# Default REVIEW_REQUIRED=false keeps legacy behavior
# ===========================================================================

class TestReviewDisabledLegacy:
    def test_review_required_defaults_to_false(self):
        from contexthub.config import Settings

        saved = os.environ.pop("REVIEW_REQUIRED", None)
        try:
            s = Settings(api_keys="k", lancedb_uri="/tmp/x", blob_dir="/tmp/b", _env_file=None)
            assert s.review_required is False
            assert s.review_approvals_required == 1
        finally:
            if saved is not None:
                os.environ["REVIEW_REQUIRED"] = saved

    def test_push_integrates_immediately(self):
        with _client_with_env({"REVIEW_REQUIRED": "false"}) as client:
            token = "LEGACY_NO_REVIEW_CC33"
            payload = _make_session("legacy-001", text=f"legacy flow {token}")
            resp = client.post("/v1/sessions", json=payload, headers=ALICE)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["review_status"] is None
            assert data["chunks_indexed"] > 0
            assert "legacy-001" in _catalog_ids(client, BOB)
            assert "legacy-001" in _cited_ids(client, token, BOB)
            # No review request was created.
            reviews = client.get("/v1/reviews", headers=BOB).json()
            assert "legacy-001" not in {r["session_id"] for r in reviews["items"]}
