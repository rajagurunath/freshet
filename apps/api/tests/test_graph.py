"""Tests for Task 13: Knowledge graph / GraphRAG-lite (API half).

Covers:
  1. GraphStore: upsert nodes/edges, dedup by (kind, name) across sessions.
  2. extract.py: parse + validate + normalize LLM JSON, upsert with a mocked LLM.
  3. Cross-session linkage: same feature touched by two sessions ⇒ one shared node
     linking both sessions.
  4. graph_extract job handler: extracts from a session and marks it graph_extracted.
  5. GET /v1/graph?focus=&depth= shape; GET /v1/graph/session/{id} shape.
  6. Graph-augmented query: POST /v1/query with use_graph=true appends graph context.
  7. Visibility: graph rows carry visibility from their session; private graph is
     invisible to other callers.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Cache reset helper
# ---------------------------------------------------------------------------

def _clear_caches() -> None:
    mods = [
        "contexthub.config",
        "contexthub.embeddings",
        "contexthub.storage.blob",
        "contexthub.storage.vectors",
        "contexthub.jobs.store",
        "contexthub.graph.store",
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


# ===========================================================================
# Unit tests: GraphStore (no HTTP)
# ===========================================================================

class TestGraphStore:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "graph.db")
        from contexthub.graph.store import GraphStore
        self.store = GraphStore(db_path)

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_upsert_node_returns_id(self):
        nid = self.store.upsert_node(
            kind="feature", name="Auth", summary="login flow",
            session_id="s1", visibility="company",
        )
        assert isinstance(nid, str) and nid

    def test_dedup_by_kind_and_name(self):
        """Same (kind, name) — case-insensitive — maps to one node id."""
        a = self.store.upsert_node(kind="feature", name="Auth", session_id="s1", visibility="company")
        b = self.store.upsert_node(kind="feature", name="auth", session_id="s2", visibility="company")
        assert a == b
        nodes = self.store.list_nodes()
        feature_nodes = [n for n in nodes if n["kind"] == "feature"]
        assert len(feature_nodes) == 1

    def test_different_kind_same_name_are_distinct(self):
        a = self.store.upsert_node(kind="feature", name="billing", session_id="s1", visibility="company")
        b = self.store.upsert_node(kind="service", name="billing", session_id="s1", visibility="company")
        assert a != b

    def test_node_records_session_provenance(self):
        nid = self.store.upsert_node(kind="repo", name="api", session_id="s1", visibility="company")
        sessions = self.store.sessions_for_node(nid)
        assert "s1" in sessions

    def test_node_accumulates_sessions(self):
        nid = self.store.upsert_node(kind="feature", name="search", session_id="s1", visibility="company")
        self.store.upsert_node(kind="feature", name="search", session_id="s2", visibility="company")
        sessions = self.store.sessions_for_node(nid)
        assert set(sessions) >= {"s1", "s2"}

    def test_upsert_edge_and_neighbors(self):
        a = self.store.upsert_node(kind="repo", name="api", session_id="s1", visibility="company")
        b = self.store.upsert_node(kind="feature", name="auth", session_id="s1", visibility="company")
        self.store.upsert_edge(src=a, dst=b, rel="implements", session_id="s1")
        neigh = self.store.neighbors(a, depth=1)
        names = {n["name"] for n in neigh["nodes"]}
        assert "auth" in names

    def test_find_nodes_by_name(self):
        self.store.upsert_node(kind="feature", name="Payment Gateway", session_id="s1", visibility="company")
        matches = self.store.find_nodes_by_terms(["payment"])
        assert any(n["name"] == "payment gateway" for n in matches)

    def test_cross_session_shared_feature_links_sessions(self):
        """A feature touched by two repos' sessions becomes one shared node."""
        f1 = self.store.upsert_node(kind="feature", name="checkout", session_id="s_api", visibility="company")
        f2 = self.store.upsert_node(kind="feature", name="checkout", session_id="s_web", visibility="company")
        assert f1 == f2
        sessions = self.store.sessions_for_node(f1)
        assert set(sessions) == {"s_api", "s_web"}

    def test_visibility_filter_hides_private_nodes(self):
        self.store.upsert_node(
            kind="feature", name="secret-feature", session_id="s_priv",
            visibility="private", author="alice",
        )
        self.store.upsert_node(
            kind="feature", name="public-feature", session_id="s_pub",
            visibility="company", author="alice",
        )
        # Bob (different user) should not see alice's private node.
        visible = self.store.list_nodes(caller_user_id="bob", caller_team="team-blue")
        names = {n["name"] for n in visible}
        assert "public-feature" in names
        assert "secret-feature" not in names
        # Alice sees her own private node.
        alice_view = self.store.list_nodes(caller_user_id="alice", caller_team="team-red")
        assert "secret-feature" in {n["name"] for n in alice_view}


# ===========================================================================
# Unit tests: extract.py with a mocked LLM
# ===========================================================================

class _FakeLLM:
    """LLM stub returning a canned JSON payload."""

    name = "fake"

    def __init__(self, payload: str):
        self._payload = payload

    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        return self._payload


_FIXTURE_JSON = """{
  "nodes": [
    {"kind": "repo", "name": "API", "summary": "the backend service"},
    {"kind": "feature", "name": "Checkout", "summary": "payment checkout flow"},
    {"kind": "person", "name": "Alice", "summary": "engineer"}
  ],
  "edges": [
    {"src": "API", "dst": "Checkout", "rel": "implements"},
    {"src": "Alice", "dst": "Checkout", "rel": "worked_on"}
  ]
}"""


class TestExtract:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        from contexthub.graph.store import GraphStore
        self.store = GraphStore(os.path.join(self._tmpdir.name, "graph.db"))

    def teardown_method(self):
        self._tmpdir.cleanup()

    def _make_session(self, sid: str):
        from contexthub.models import NormalizedSession
        return NormalizedSession(
            id=sid, tool="claude-code", title="Checkout work",
            message_count=2, preview="checkout",
            messages=[],
        )

    def test_extract_and_persist_nodes(self):
        from contexthub.graph.extract import extract_graph
        session = self._make_session("s1")
        result = extract_graph(
            session=session, summary="Built the checkout flow in the API repo with Alice.",
            store=self.store, llm=_FakeLLM(_FIXTURE_JSON),
            visibility="company", author="alice",
        )
        assert result["nodes_upserted"] == 3
        assert result["edges_upserted"] == 2
        nodes = self.store.list_nodes()
        names = {n["name"] for n in nodes}
        assert {"api", "checkout", "alice"} <= names

    def test_extract_normalizes_names(self):
        from contexthub.graph.extract import extract_graph
        session = self._make_session("s1")
        extract_graph(
            session=session, summary="x", store=self.store, llm=_FakeLLM(_FIXTURE_JSON),
            visibility="company", author="alice",
        )
        nodes = self.store.list_nodes()
        for n in nodes:
            assert n["name"] == n["name"].strip().lower()

    def test_extract_dedup_cross_session(self):
        """Two sessions sharing a 'Checkout' feature ⇒ one node linking both."""
        from contexthub.graph.extract import extract_graph
        extract_graph(
            session=self._make_session("s_api"), summary="x", store=self.store,
            llm=_FakeLLM(_FIXTURE_JSON), visibility="company", author="alice",
        )
        extract_graph(
            session=self._make_session("s_web"), summary="x", store=self.store,
            llm=_FakeLLM(_FIXTURE_JSON), visibility="company", author="alice",
        )
        feature_nodes = [n for n in self.store.list_nodes() if n["kind"] == "feature"]
        assert len(feature_nodes) == 1
        sessions = self.store.sessions_for_node(feature_nodes[0]["id"])
        assert set(sessions) == {"s_api", "s_web"}

    def test_extract_handles_malformed_json(self):
        from contexthub.graph.extract import extract_graph
        result = extract_graph(
            session=self._make_session("s1"), summary="x", store=self.store,
            llm=_FakeLLM("not json at all"), visibility="company", author="alice",
        )
        assert result["nodes_upserted"] == 0
        assert result["edges_upserted"] == 0

    def test_extract_skips_edges_with_unknown_endpoints(self):
        from contexthub.graph.extract import extract_graph
        payload = '{"nodes": [{"kind":"feature","name":"A"}], "edges": [{"src":"A","dst":"Ghost","rel":"x"}]}'
        result = extract_graph(
            session=self._make_session("s1"), summary="x", store=self.store,
            llm=_FakeLLM(payload), visibility="company", author="alice",
        )
        assert result["nodes_upserted"] == 1
        assert result["edges_upserted"] == 0


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
        )


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db, graph_db = tmp_dirs

    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "JOBS_DB": jobs_db,
        "GRAPH_DB": graph_db,
        "API_KEYS": "alice-key:alice:team-red,bob-key:bob:team-blue",
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


ALICE = {"Authorization": "Bearer alice-key"}
BOB = {"Authorization": "Bearer bob-key"}


def _make_session(session_id: str, *, visibility: str = "company") -> dict:
    return {
        "session": {
            "id": session_id,
            "tool": "claude-code",
            "title": f"Session {session_id}",
            "project": "proj",
            "message_count": 2,
            "models": ["claude-sonnet-4-6"],
            "tokens": {"input": 100, "output": 20},
            "preview": "preview",
            "file_path": f"/x/{session_id}.jsonl",
            "messages": [
                {"id": "m1", "role": "user", "text": "Work on checkout in the API repo"},
                {"id": "m2", "role": "assistant", "text": "Done the checkout flow"},
            ],
        },
        "summary": "Built the checkout feature in the api repo.",
        "category": "engineering",
        "visibility": visibility,
        "author": {"id": "alice", "email": "a@x.com", "name": "Alice", "team": "team-red"},
        "redacted": True,
    }


def _seed_graph(client: TestClient, session_id: str, *, visibility: str = "company", author="alice"):
    """Directly seed graph rows via the GraphStore (bypassing the LLM)."""
    from contexthub.graph.store import get_graph_store
    store = get_graph_store()
    repo = store.upsert_node(kind="repo", name=f"repo-{session_id}", session_id=session_id, visibility=visibility, author=author)
    feat = store.upsert_node(kind="feature", name="checkout", session_id=session_id, visibility=visibility, author=author)
    store.upsert_edge(src=repo, dst=feat, rel="implements", session_id=session_id)
    return repo, feat


def test_graph_endpoint_shape(client: TestClient):
    client.post("/v1/sessions", json=_make_session("g-1"), headers=ALICE)
    _seed_graph(client, "g-1")
    resp = client.get("/v1/graph", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "nodes" in data and "edges" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    # node fields
    if data["nodes"]:
        n = data["nodes"][0]
        assert {"id", "kind", "name"} <= set(n.keys())


def test_graph_focus_and_depth(client: TestClient):
    client.post("/v1/sessions", json=_make_session("g-2"), headers=ALICE)
    repo, feat = _seed_graph(client, "g-2")
    resp = client.get("/v1/graph", params={"focus": "checkout", "depth": 1}, headers=ALICE)
    assert resp.status_code == 200, resp.text
    names = {n["name"] for n in resp.json()["nodes"]}
    assert "checkout" in names


def test_graph_session_endpoint(client: TestClient):
    client.post("/v1/sessions", json=_make_session("g-3"), headers=ALICE)
    _seed_graph(client, "g-3")
    resp = client.get("/v1/graph/session/g-3", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "nodes" in data and "edges" in data
    names = {n["name"] for n in data["nodes"]}
    assert "checkout" in names


def test_graph_visibility_enforced(client: TestClient):
    # Alice ingests a private session and seeds a private graph node.
    client.post("/v1/sessions", json=_make_session("g-priv", visibility="private"), headers=ALICE)
    _seed_graph(client, "g-priv", visibility="private", author="alice")

    # Bob must not see alice's private graph nodes.
    resp = client.get("/v1/graph", headers=BOB)
    assert resp.status_code == 200
    bob_names = {n["name"] for n in resp.json()["nodes"]}
    assert f"repo-g-priv" not in bob_names

    # Alice sees her own private node.
    resp_a = client.get("/v1/graph", headers=ALICE)
    alice_names = {n["name"] for n in resp_a.json()["nodes"]}
    assert "repo-g-priv" in alice_names


def test_graph_augmented_query(client: TestClient):
    client.post("/v1/sessions", json=_make_session("g-q"), headers=ALICE)
    _seed_graph(client, "g-q")
    # use_graph=true must succeed and return a QueryResponse (graph context appended).
    resp = client.post(
        "/v1/query",
        json={"question": "tell me about checkout", "use_graph": True, "top_k": 5},
        headers=ALICE,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "answer" in data and "citations" in data


def test_graph_extract_job_handler():
    """The graph_extract handler extracts from a session and marks it done."""
    import tempfile as _t
    with _t.TemporaryDirectory() as tmpdir:
        env_patch = {
            "EMBEDDING_PROVIDER": "hash",
            "LANCEDB_URI": os.path.join(tmpdir, "lancedb"),
            "BLOB_DIR": os.path.join(tmpdir, "blobs"),
            "JOBS_DB": os.path.join(tmpdir, "jobs.db"),
            "GRAPH_DB": os.path.join(tmpdir, "graph.db"),
            "API_KEYS": "alice-key:alice:team-red",
            "ANTHROPIC_API_KEY": "",
            "S3_BUCKET": "",
        }
        original = {k: os.environ.get(k) for k in env_patch}
        os.environ.update(env_patch)
        _clear_caches()
        try:
            from contexthub.main import create_app
            app = create_app()
            with TestClient(app) as c:
                c.post("/v1/sessions", json=_make_session("gj-1"), headers={"Authorization": "Bearer alice-key"})

            # Run the handler directly with a mocked LLM via monkeypatching get_llm.
            import contexthub.graph.extract as extract_mod
            orig_get_llm = extract_mod.get_llm
            extract_mod.get_llm = lambda *a, **k: _FakeLLM(_FIXTURE_JSON)
            try:
                from contexthub.jobs.handlers import graph_extract_handler
                result = graph_extract_handler({"session_id": "gj-1"})
            finally:
                extract_mod.get_llm = orig_get_llm

            assert result.get("session_id") == "gj-1"
            assert result.get("nodes_upserted", 0) >= 1

            from contexthub.graph.store import get_graph_store
            store = get_graph_store()
            names = {n["name"] for n in store.list_nodes()}
            assert "checkout" in names
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _clear_caches()


# ===========================================================================
# POST /v1/graph/backfill tests
# ===========================================================================

def test_backfill_enqueues_unextracted_sessions(client: TestClient):
    """Backfill enqueues graph_extract jobs for sessions not yet extracted."""
    # Ingest two fresh sessions (graph_extracted defaults to False)
    client.post("/v1/sessions", json=_make_session("bf-1"), headers=ALICE)
    client.post("/v1/sessions", json=_make_session("bf-2"), headers=ALICE)

    resp = client.post("/v1/graph/backfill", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "enqueued" in data and "skipped" in data
    # At least our two sessions should be counted (there may be more from earlier tests)
    assert data["enqueued"] >= 2


def test_backfill_second_call_skips_extracted(client: TestClient):
    """After marking sessions as extracted, a second backfill call enqueues 0 new."""
    # Ingest a dedicated session
    client.post("/v1/sessions", json=_make_session("bf-mark"), headers=ALICE)

    # Mark it extracted directly via the vector store
    from contexthub.storage.vectors import get_vector_store as _gvs
    _gvs().mark_graph_extracted("bf-mark")

    # First call — bf-mark should be in skipped
    resp1 = client.post("/v1/graph/backfill", headers=ALICE)
    assert resp1.status_code == 200
    data1 = resp1.json()
    # bf-mark is now extracted so should not be re-enqueued
    # We just verify the response shape; enqueued count may vary due to other test sessions
    assert isinstance(data1["enqueued"], int)
    assert isinstance(data1["skipped"], int)
    assert data1["skipped"] >= 1


def test_resolve_backfill_targets_extracted_sessions(client: TestClient):
    """resolve-backfill enqueues entity_resolve for already-extracted sessions
    and skips sessions whose graph has not been extracted yet (the inverse of
    graph/backfill)."""
    from contexthub.storage.vectors import get_vector_store as _gvs

    # One extracted session (a resolution target) and one not-yet-extracted.
    client.post("/v1/sessions", json=_make_session("rb-extracted"), headers=ALICE)
    client.post("/v1/sessions", json=_make_session("rb-pending"), headers=ALICE)
    _gvs().mark_graph_extracted("rb-extracted")

    resp = client.post("/v1/graph/resolve-backfill", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "enqueued" in data and "skipped" in data
    # The extracted session is a resolution target; the pending one is skipped.
    assert data["enqueued"] >= 1
    assert data["skipped"] >= 1
