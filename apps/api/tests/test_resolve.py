"""Tests for Slice 1: cross-session graph memory (entity resolution).

When two separate sessions build their own graphs and name the same concept
differently — session A's feature "checkout" vs session B's "payment-checkout" —
``resolve_session_nodes`` links them with a reversible ``same_as`` edge so the
two session graphs connect.

The integration test below drives the *real* code paths: the real ``GraphStore``
on a tmp SQLite db, the real ``extract_graph`` upsert path (fed by a stubbed LLM,
exactly as the app does in CI), and the real ``resolve_session_nodes``. Only the
embedder is a deterministic stub so the cosine score for the true match clears
``er_high_threshold`` reliably (the offline 'hash' embedder is not semantically
meaningful, so a real MiniLM would be required for a non-stubbed score — out of
scope for a unit/integration test).
"""

from __future__ import annotations

import os
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Stubs mirroring the conventions in tests/test_graph.py
# ---------------------------------------------------------------------------

class _FakeLLM:
    """LLM stub returning a canned graph-extraction JSON payload."""

    name = "fake"

    def __init__(self, payload: str):
        self._payload = payload

    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        return self._payload


class _StubEmbedder:
    """Deterministic embedder: maps known strings to fixed unit vectors.

    "checkout" and "payment-checkout" are mapped to (nearly) the same vector so
    their cosine clears er_high_threshold; everything else gets an orthogonal
    direction so it never accidentally links.
    """

    dim = 4

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            low = t.lower()
            if "checkout" in low:
                # Same direction for both checkout / payment-checkout phrasings.
                out.append([1.0, 0.0, 0.0, 0.0])
            else:
                out.append([0.0, 1.0, 0.0, 0.0])
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


class _Settings:
    er_high_threshold = 0.85
    er_low_threshold = 0.55


def _payload(feature_name: str) -> str:
    """Graph-extraction JSON yielding one repo and one feature node."""
    return (
        '{"nodes": ['
        '{"kind": "repo", "name": "api", "summary": "backend service"},'
        f'{{"kind": "feature", "name": "{feature_name}", "summary": "payment checkout flow"}}'
        '],'
        '"edges": [{"src": "api", "dst": "' + feature_name + '", "rel": "implements"}]}'
    )


def _make_session(sid: str):
    from contexthub.models import NormalizedSession
    return NormalizedSession(
        id=sid, tool="claude-code", title="Checkout work",
        message_count=2, preview="checkout", messages=[],
    )


def test_resolve_links_checkout_to_payment_checkout():
    """Session A's 'checkout' and session B's 'payment-checkout' get a same_as edge."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from contexthub.graph.store import GraphStore
        from contexthub.graph.extract import extract_graph
        from contexthub.graph.resolve import resolve_session_nodes

        store = GraphStore(os.path.join(tmpdir, "graph.db"))

        # Session A builds a graph naming the feature "checkout".
        extract_graph(
            session=_make_session("s_a"), summary="built checkout in the api repo",
            store=store, llm=_FakeLLM(_payload("checkout")),
            visibility="company", author="alice",
        )
        # Session B builds a graph naming the same concept "payment-checkout".
        extract_graph(
            session=_make_session("s_b"), summary="worked on payment-checkout in api",
            store=store, llm=_FakeLLM(_payload("payment-checkout")),
            visibility="company", author="alice",
        )

        # Identify the two feature node ids.
        features = {n["name"]: n["id"] for n in store.list_nodes() if n["kind"] == "feature"}
        assert "checkout" in features and "payment-checkout" in features
        checkout_id = features["checkout"]
        payment_checkout_id = features["payment-checkout"]
        assert checkout_id != payment_checkout_id

        # Run entity resolution for session B against the global graph.
        result = resolve_session_nodes(
            session_id="s_b",
            store=store,
            embedder=_StubEmbedder(),
            llm=_FakeLLM("{}"),
            settings=_Settings(),
        )
        assert result["same_as_added"] == 1, result

        # A same_as edge now connects the two feature nodes (either direction).
        same_as = [e for e in store.list_edges() if e["rel"] == "same_as"]
        endpoints = {(e["src"], e["dst"]) for e in same_as}
        assert (
            (payment_checkout_id, checkout_id) in endpoints
            or (checkout_id, payment_checkout_id) in endpoints
        ), endpoints

        # neighbors(checkout_id) reaches payment_checkout_id by crossing same_as.
        neigh = store.neighbors(checkout_id, depth=1)
        reached = {n["id"] for n in neigh["nodes"]}
        assert payment_checkout_id in reached

        # And the canonical id collapses the pair to one representative.
        from contexthub.graph.resolve import get_canonical_id
        assert get_canonical_id(store, checkout_id) == get_canonical_id(store, payment_checkout_id)


def test_resolve_no_match_below_threshold():
    """Unrelated same-kind nodes are not linked (rejected)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from contexthub.graph.store import GraphStore
        from contexthub.graph.resolve import resolve_session_nodes

        store = GraphStore(os.path.join(tmpdir, "graph.db"))
        # Two same-kind nodes whose names share enough n-grams to block but whose
        # embeddings diverge (stub maps non-checkout names to an orthogonal axis).
        store.upsert_node(kind="feature", name="checkout", session_id="s1", visibility="company")
        store.upsert_node(kind="feature", name="checkin", session_id="s2", visibility="company")

        result = resolve_session_nodes(
            session_id="s2", store=store, embedder=_StubEmbedder(),
            llm=_FakeLLM("{}"), settings=_Settings(),
        )
        assert result["same_as_added"] == 0
        assert not [e for e in store.list_edges() if e["rel"] == "same_as"]


def test_resolve_links_private_nodes_for_same_author():
    """A private session's nodes still resolve (regression: list_nodes() used to
    drop team/private rows, so private sessions got no cross-session linking)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from contexthub.graph.store import GraphStore
        from contexthub.graph.resolve import resolve_session_nodes

        store = GraphStore(os.path.join(tmpdir, "graph.db"))
        # Both nodes are PRIVATE to alice — never visible under the default
        # (company-only) clause that the old implementation applied.
        a = store.upsert_node(kind="feature", name="checkout", session_id="s1",
                              visibility="private", author="alice")
        b = store.upsert_node(kind="feature", name="payment-checkout", session_id="s2",
                              visibility="private", author="alice")

        result = resolve_session_nodes(
            session_id="s2", store=store, embedder=_StubEmbedder(),
            llm=_FakeLLM("{}"), settings=_Settings(),
            caller_user_id="alice", caller_team=None,
        )
        assert result["same_as_added"] == 1, result
        endpoints = {(e["src"], e["dst"]) for e in store.list_edges(caller_user_id="alice")
                     if e["rel"] == "same_as"}
        assert (a, b) in endpoints or (b, a) in endpoints, endpoints


def test_resolve_is_idempotent_on_rerun():
    """Re-running resolution does not duplicate edges or re-count an existing link."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from contexthub.graph.store import GraphStore
        from contexthub.graph.resolve import resolve_session_nodes

        store = GraphStore(os.path.join(tmpdir, "graph.db"))
        store.upsert_node(kind="feature", name="checkout", session_id="s1", visibility="company")
        store.upsert_node(kind="feature", name="payment-checkout", session_id="s2", visibility="company")

        kwargs = dict(store=store, embedder=_StubEmbedder(),
                      llm=_FakeLLM("{}"), settings=_Settings())
        first = resolve_session_nodes(session_id="s2", **kwargs)
        assert first["same_as_added"] == 1
        # Second pass: the pair is already linked → nothing added, no dup rows.
        second = resolve_session_nodes(session_id="s2", **kwargs)
        assert second["same_as_added"] == 0, second
        same_as = [e for e in store.list_edges() if e["rel"] == "same_as"]
        assert len(same_as) == 1, same_as
