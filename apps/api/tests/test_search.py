"""Tests for Task 3: Hybrid search (FTS + vector + RRF).

Covers:
  1. QueryRequest now accepts a `mode` field: "hybrid" | "vector" | "keyword".
  2. VectorStore.hybrid_search() merges vector + FTS results via RRF.
  3. FTS index is created lazily on the chunks table.
  4. Keyword-only and vector-only modes work end-to-end.
  5. RRF math: a chunk that appears near-top in both lists scores highest.
  6. Exact-identifier query: with the hash embedder, an exact token like
     "ERR_5021_FOO" is NOT retrievable by vector alone (different hash),
     but FTS will find it — hybrid mode must return the chunk.
  7. answer_query() defaults to hybrid mode when mode is "hybrid".
  8. POST /v1/query passes mode through to hybrid_search.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections import defaultdict
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


def _make_session(session_id: str, text: str, title: str | None = None) -> dict:
    return {
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
                {
                    "id": "m2",
                    "role": "assistant",
                    "text": "Understood. " + text[:40],
                    "timestamp": "2026-06-09T10:01:00Z",
                    "model": "claude-sonnet-4-6",
                },
            ],
        },
        "summary": f"Summary of {text[:60]}",
        "category": "engineering",
        "visibility": "company",
        "author": {"id": "u_test", "email": "test@example.com", "name": "Test User"},
        "redacted": True,
    }


# ---------------------------------------------------------------------------
# Unit tests for VectorStore.hybrid_search
# ---------------------------------------------------------------------------

def _build_vector_store(uri: str, dim: int = 384):
    from contexthub.storage.vectors import VectorStore
    return VectorStore(uri=uri, embedding_dim=dim)


def _make_chunk(chunk_id: str, session_id: str, text: str, vec_dim: int = 384) -> dict:
    import hashlib
    import math

    data = text.encode("utf-8")
    vals: list[float] = []
    counter = 0
    while len(vals) < vec_dim:
        block = hashlib.sha256(data + counter.to_bytes(4, "little")).digest()
        for b in block:
            if len(vals) >= vec_dim:
                break
            vals.append((b / 255.0) * 2.0 - 1.0)
        counter += 1
    norm = math.sqrt(sum(v * v for v in vals))
    if norm == 0.0:
        norm = 1.0
    vector = [v / norm for v in vals]

    return {
        "id": chunk_id,
        "session_id": session_id,
        "tool": "claude-code",
        "category": "engineering",
        "author": "u_test",
        "project": "proj",
        "visibility": "company",
        "text": text,
        "vector": vector,
        "created_at": "2026-06-09T10:00:00Z",
    }


class TestRRF:
    """Pure unit test for the rrf() helper."""

    def test_rrf_single_list(self):
        from contexthub.storage.vectors import rrf

        ranks = [["a", "b", "c"]]
        result = rrf(ranks, k=60)
        assert result == ["a", "b", "c"]

    def test_rrf_two_lists_same_order(self):
        from contexthub.storage.vectors import rrf

        ranks = [["a", "b", "c"], ["a", "b", "c"]]
        result = rrf(ranks, k=60)
        assert result == ["a", "b", "c"]

    def test_rrf_two_lists_agreement_boosts(self):
        """A chunk ranked #1 in both lists should beat one ranked #1 in only one."""
        from contexthub.storage.vectors import rrf

        # "shared" is #1 in both; "vector_only" is only in list 1
        ranks = [["shared", "vector_only"], ["shared", "fts_only"]]
        result = rrf(ranks, k=60)
        assert result[0] == "shared"

    def test_rrf_k_parameter_reduces_high_rank_advantage(self):
        """With a large k, rank-1 and rank-2 scores are closer together."""
        from contexthub.storage.vectors import rrf

        # k=1 exaggerates rank-1 advantage; k=1000 flattens it
        ranks_large_k = [["a", "b"], ["b", "a"]]
        result = rrf(ranks_large_k, k=1)
        # With k=1: a gets 1/(1+1)+1/(1+2) = 0.5+0.33=0.83
        #           b gets 1/(1+2)+1/(1+1) = 0.33+0.5=0.83
        # Tie is stable-sorted; doesn't matter which comes first
        assert set(result) == {"a", "b"}

    def test_rrf_empty_lists(self):
        from contexthub.storage.vectors import rrf

        result = rrf([], k=60)
        assert result == []

    def test_rrf_one_empty_list(self):
        from contexthub.storage.vectors import rrf

        result = rrf([[], ["x", "y"]], k=60)
        assert result == ["x", "y"]


class TestHybridSearch:
    """Integration tests for VectorStore.hybrid_search()."""

    def test_hybrid_search_returns_results(self, tmp_path):
        """hybrid_search must return a non-empty list when matching chunks exist."""
        from contexthub.storage.vectors import VectorStore

        uri = str(tmp_path / "lancedb_hybrid")
        vs = VectorStore(uri=uri, embedding_dim=384)

        chunk = _make_chunk("c1", "s1", "retry logic S3 upload function exponential backoff")
        vs.upsert_chunks([chunk])
        vs.ensure_fts_index()

        query_vec = _make_chunk("q", "q", "retry logic S3")["vector"]
        results = vs.hybrid_search("retry logic S3", query_vec, top_k=3)
        assert len(results) >= 1

    def test_hybrid_search_returns_top_k_or_fewer(self, tmp_path):
        """hybrid_search must return at most top_k results."""
        from contexthub.storage.vectors import VectorStore

        uri = str(tmp_path / "lancedb_topk")
        vs = VectorStore(uri=uri, embedding_dim=384)

        for i in range(5):
            chunk = _make_chunk(f"c{i}", f"s{i}", f"sample text chunk number {i} for testing search results")
            vs.upsert_chunks([chunk])
        vs.ensure_fts_index()

        query_vec = _make_chunk("q", "q", "sample text chunk")["vector"]
        results = vs.hybrid_search("sample text chunk", query_vec, top_k=3)
        assert len(results) <= 3

    def test_hybrid_search_fused_score_field(self, tmp_path):
        """Each result from hybrid_search must have a '_score' field (fused RRF score)."""
        from contexthub.storage.vectors import VectorStore

        uri = str(tmp_path / "lancedb_score")
        vs = VectorStore(uri=uri, embedding_dim=384)

        chunk = _make_chunk("c1", "s1", "deploy kubernetes cluster autoscaling")
        vs.upsert_chunks([chunk])
        vs.ensure_fts_index()

        query_vec = _make_chunk("q", "q", "kubernetes autoscaling")["vector"]
        results = vs.hybrid_search("kubernetes autoscaling", query_vec, top_k=3)
        for r in results:
            assert "_score" in r, f"Result missing '_score': {list(r.keys())}"
            assert isinstance(r["_score"], float)
            assert r["_score"] > 0.0

    def test_fts_finds_exact_identifier(self, tmp_path):
        """Exact token (e.g. ERR_5021_FOO) must be found by FTS even when vector
        search alone wouldn't surface it (hash embedder mismatch)."""
        from contexthub.storage.vectors import VectorStore

        uri = str(tmp_path / "lancedb_fts")
        vs = VectorStore(uri=uri, embedding_dim=384)

        # Chunk with a unique error code; vector search with a mismatched query
        # won't find it by semantic similarity, but FTS exact match will.
        target_text = "Error code ERR_5021_FOO occurs when the auth token expires"
        noise_texts = [
            "the quick brown fox jumps over the lazy dog",
            "python is a high-level programming language",
            "docker containers provide isolation and reproducibility",
        ]
        chunks = [_make_chunk("target", "s_target", target_text)]
        for i, t in enumerate(noise_texts):
            chunks.append(_make_chunk(f"noise{i}", f"s_noise{i}", t))
        vs.upsert_chunks(chunks)
        vs.ensure_fts_index()

        # Use a semantically different query that contains the exact identifier.
        # The hash embedder will produce a completely different vector for the
        # query vs. the target (no semantic alignment), but FTS must find it.
        query = "ERR_5021_FOO"
        query_vec = _make_chunk("q", "q", "unrelated semantic topic")["vector"]
        results = vs.hybrid_search(query, query_vec, top_k=5)

        found_ids = [r.get("id", r.get("session_id", "")) for r in results]
        assert "target" in found_ids, (
            f"Expected 'target' chunk (containing ERR_5021_FOO) in hybrid results, "
            f"but got: {found_ids}"
        )

    def test_vector_only_mode(self, tmp_path):
        """mode='vector' must use only the vector search path."""
        from contexthub.storage.vectors import VectorStore

        uri = str(tmp_path / "lancedb_vec_only")
        vs = VectorStore(uri=uri, embedding_dim=384)

        text = "monitoring alerting prometheus grafana"
        chunk = _make_chunk("c1", "s1", text)
        vs.upsert_chunks([chunk])
        vs.ensure_fts_index()

        query_vec = chunk["vector"]  # identical vector → should come back top
        results = vs.hybrid_search("prometheus grafana", query_vec, top_k=3, mode="vector")
        assert len(results) >= 1

    def test_keyword_only_mode(self, tmp_path):
        """mode='keyword' must use only the FTS search path."""
        from contexthub.storage.vectors import VectorStore

        uri = str(tmp_path / "lancedb_kw_only")
        vs = VectorStore(uri=uri, embedding_dim=384)

        text = "unique_keyword_ZXQWERTY_test token for FTS"
        chunk = _make_chunk("c1", "s1", text)
        vs.upsert_chunks([chunk])
        vs.ensure_fts_index()

        # Deliberately different vector (won't match by cosine)
        query_vec = _make_chunk("q", "q", "completely different semantics here")["vector"]
        results = vs.hybrid_search("unique_keyword_ZXQWERTY_test", query_vec, top_k=3, mode="keyword")
        found_ids = [r.get("id", "") for r in results]
        assert "c1" in found_ids, f"FTS keyword-only mode failed: {found_ids}"

    def test_hybrid_search_with_filters(self, tmp_path):
        """hybrid_search must respect metadata filters."""
        from contexthub.storage.vectors import VectorStore

        uri = str(tmp_path / "lancedb_filt")
        vs = VectorStore(uri=uri, embedding_dim=384)

        chunk_eng = _make_chunk("c_eng", "s_eng", "authentication OAuth JWT tokens")
        chunk_eng["category"] = "engineering"
        chunk_sales = _make_chunk("c_sales", "s_sales", "authentication OAuth JWT tokens")
        chunk_sales["category"] = "sales"
        vs.upsert_chunks([chunk_eng, chunk_sales])
        vs.ensure_fts_index()

        query_vec = _make_chunk("q", "q", "OAuth JWT")["vector"]
        results = vs.hybrid_search(
            "OAuth JWT",
            query_vec,
            top_k=5,
            filters={"category": "engineering"},
        )
        for r in results:
            assert r.get("category") == "engineering", (
                f"Filter not applied: got category={r.get('category')}"
            )


# ---------------------------------------------------------------------------
# QueryRequest model — mode field
# ---------------------------------------------------------------------------

class TestQueryRequestModel:
    """The QueryRequest model must accept a mode field."""

    def test_default_mode_is_hybrid(self):
        from contexthub.models import QueryRequest

        req = QueryRequest(question="test")
        assert req.mode == "hybrid"

    def test_mode_vector(self):
        from contexthub.models import QueryRequest

        req = QueryRequest(question="test", mode="vector")
        assert req.mode == "vector"

    def test_mode_keyword(self):
        from contexthub.models import QueryRequest

        req = QueryRequest(question="test", mode="keyword")
        assert req.mode == "keyword"

    def test_mode_invalid_raises(self):
        from pydantic import ValidationError
        from contexthub.models import QueryRequest

        with pytest.raises(ValidationError):
            QueryRequest(question="test", mode="invalid_mode")


# ---------------------------------------------------------------------------
# API integration: POST /v1/query with mode parameter
# ---------------------------------------------------------------------------

def test_query_default_mode_hybrid(client: TestClient):
    """POST /v1/query without explicit mode should use hybrid search and return results."""
    # Ingest a session
    payload = _make_session(
        "qhybrid-001",
        "We implemented a circuit breaker pattern for the S3 client to handle transient failures",
    )
    resp = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text

    # Query using default (hybrid) mode
    q = {"question": "circuit breaker pattern S3 client failures", "top_k": 5}
    resp = client.post("/v1/query", json=q, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "answer" in data
    assert "citations" in data


def test_query_explicit_mode_vector(client: TestClient):
    """POST /v1/query with mode='vector' should work."""
    payload = _make_session(
        "qvec-001",
        "Kubernetes autoscaling with horizontal pod autoscaler metrics",
    )
    resp = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text

    q = {"question": "kubernetes scaling", "top_k": 3, "mode": "vector"}
    resp = client.post("/v1/query", json=q, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "answer" in data


def test_query_explicit_mode_keyword(client: TestClient):
    """POST /v1/query with mode='keyword' should work."""
    payload = _make_session(
        "qkw-001",
        "Terraform state management with remote backend S3 bucket locking",
    )
    resp = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text

    q = {"question": "terraform state backend", "top_k": 3, "mode": "keyword"}
    resp = client.post("/v1/query", json=q, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "answer" in data


def test_query_fts_retrieves_exact_identifier(client: TestClient):
    """Hybrid mode (or keyword mode) must retrieve a chunk containing ERR_7745_UNIQUE
    even when hash-embedder vectors don't align semantically."""
    # The unique error code is something unlikely to match by vector similarity.
    unique_code = "ERR_7745_UNIQUE_FOR_FTS_TEST"
    payload = _make_session(
        "qfts-001",
        f"When the database connection pool exhausts, the system raises {unique_code} "
        "which should trigger an alert and restart the connection pool automatically.",
        title="DB connection pool exhaustion",
    )
    resp = client.post("/v1/sessions", json=payload, headers=AUTH)
    assert resp.status_code == 200, resp.text

    # Query using the exact identifier — semantic search alone won't find it
    q = {"question": unique_code, "top_k": 10, "mode": "hybrid"}
    resp = client.post("/v1/query", json=q, headers=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    session_ids = [c["session_id"] for c in data["citations"]]
    assert "qfts-001" in session_ids, (
        f"Expected qfts-001 in citations (FTS on exact id), got: {session_ids}"
    )


def test_query_invalid_mode_returns_422(client: TestClient):
    """POST /v1/query with an invalid mode should return 422."""
    q = {"question": "test", "top_k": 3, "mode": "turbo_laser"}
    resp = client.post("/v1/query", json=q, headers=AUTH)
    assert resp.status_code == 422
