"""Tests for cross-session correlation signals beyond the graph (Slice S6)."""

from __future__ import annotations

import os
import tempfile

from contexthub.graph.correlate import (
    ppmi_entity_pairs,
    related_sessions,
    session_similarity_edges,
)


def test_session_similarity_mutual_knn():
    # Three sessions: A and B nearly identical; C orthogonal.
    sids = ["A", "B", "C"]
    vecs = [[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]]
    edges = session_similarity_edges(sids, vecs, k=2, min_sim=0.3, mutual=True)
    pairs = {(e.src, e.dst) for e in edges}
    assert ("A", "B") in pairs
    # C should not be strongly linked to A/B.
    assert ("A", "C") not in pairs and ("B", "C") not in pairs


def test_related_sessions_ranks_by_similarity():
    sids = ["A", "B", "C"]
    vecs = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
    rel = related_sessions("A", sids, vecs, k=2, min_sim=0.0)
    assert rel[0][0] == "B"  # most similar first
    assert [sid for sid, _ in rel] == ["B", "C"]


def test_ppmi_surfaces_tight_cooccurrence():
    """Two entities that always appear together score positive PPMI; an entity
    spread across unrelated sessions does not pair with them."""
    with tempfile.TemporaryDirectory() as tmp:
        from contexthub.graph.store import GraphStore

        store = GraphStore(os.path.join(tmp, "graph.db"))
        # stripe + payments-api always co-occur (sessions s1, s2).
        for sid in ("s1", "s2"):
            store.upsert_node(kind="tool", name="stripe", session_id=sid, visibility="company")
            store.upsert_node(kind="service", name="payments-api", session_id=sid, visibility="company")
        # redis appears in unrelated sessions.
        store.upsert_node(kind="tool", name="redis", session_id="s3", visibility="company")
        store.upsert_node(kind="tool", name="redis", session_id="s4", visibility="company")

        pairs = ppmi_entity_pairs(store, min_cooccur=2)
        top = {(p.a, p.b) for p in pairs}
        assert ("service:payments-api", "tool:stripe") in top or \
               ("tool:stripe", "service:payments-api") in top
        # redis co-occurs with nothing twice → no pair involving redis.
        assert not any("redis" in p.a or "redis" in p.b for p in pairs)


def test_ppmi_respects_min_cooccur_floor():
    with tempfile.TemporaryDirectory() as tmp:
        from contexthub.graph.store import GraphStore

        store = GraphStore(os.path.join(tmp, "graph.db"))
        # a + b co-occur in exactly one session; other sessions dilute the priors
        # (so P(a), P(b) < 1 and the pair can score positive PMI when floor=1).
        store.upsert_node(kind="tool", name="a", session_id="s1", visibility="company")
        store.upsert_node(kind="tool", name="b", session_id="s1", visibility="company")
        store.upsert_node(kind="tool", name="c", session_id="s2", visibility="company")
        store.upsert_node(kind="tool", name="d", session_id="s3", visibility="company")
        # Default floor of 2 → the single co-occurrence is dropped.
        assert ppmi_entity_pairs(store, min_cooccur=2) == []
        # Floor 1 → the single co-occurrence surfaces with positive PPMI.
        pairs = ppmi_entity_pairs(store, min_cooccur=1)
        assert any({p.a, p.b} == {"tool:a", "tool:b"} for p in pairs)


# ---------------------------------------------------------------------------
# PPMI co-occurrence edges replace the per-session star (GraphRAG overhaul)
# ---------------------------------------------------------------------------

def test_refresh_cooccur_edges_replaces_star_with_ppmi(tmp_path):
    from contexthub.graph.correlate import refresh_cooccur_edges
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    # redis+celery always together (3/6 sessions); postgres everywhere (6/6).
    for i in range(3):
        store.upsert_node(kind="tool", name="redis", session_id=f"pair{i}")
        store.upsert_node(kind="tool", name="celery", session_id=f"pair{i}")
    for i in range(6):
        sid = f"pair{i}" if i < 3 else f"solo{i}"
        store.upsert_node(kind="tool", name="postgres", session_id=sid)
    # a stale star edge that must be replaced
    nodes = {n["name"]: n["id"] for n in store.list_nodes()}
    store.upsert_edge(src=nodes["postgres"], dst=nodes["redis"], rel="co_occurs")

    written = refresh_cooccur_edges(store, min_cooccur=2)
    assert written >= 1
    edges = store.list_edges()
    cooc = [e for e in edges if e["rel"] == "co_occurs"]
    pairs = {frozenset((e["src"], e["dst"])) for e in cooc}
    # redis↔celery survives (high PPMI); the stale postgres↔redis star is gone
    assert frozenset((nodes["redis"], nodes["celery"])) in pairs
    assert frozenset((nodes["postgres"], nodes["redis"])) not in pairs


def test_delete_edges_by_rel(tmp_path):
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    a = store.upsert_node(kind="tool", name="a", session_id="s1")
    b = store.upsert_node(kind="tool", name="b", session_id="s1")
    store.upsert_edge(src=a, dst=b, rel="co_occurs")
    store.upsert_edge(src=a, dst=b, rel="uses")
    assert store.delete_edges_by_rel("co_occurs") == 1
    assert [e["rel"] for e in store.list_edges()] == ["uses"]
