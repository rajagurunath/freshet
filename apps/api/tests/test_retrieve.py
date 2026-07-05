"""Tests for the graph retrieval arm (Slice S3).

The decisive case is *bridge* retrieval: a question that lexically points at one
session, whose answer is in a different session reachable only via a shared
entity. ``graph_search`` seeded from the vector hit must surface the answer
session; plain provenance lookups would not.
"""

from __future__ import annotations

import os
import tempfile

from contexthub.graph.retrieve import graph_search, question_terms


def test_question_terms_drops_stopwords():
    terms = question_terms("How does the checkout flow call Stripe?")
    assert "checkout" in terms and "stripe" in terms
    assert "how" not in terms and "the" not in terms


def test_graph_search_bridges_via_shared_entity():
    """blog session shares 'checkout' with the impl session; seeding the walk
    from the blog session must surface impl even though impl shares no query word."""
    with tempfile.TemporaryDirectory() as tmp:
        from contexthub.graph.store import GraphStore

        store = GraphStore(os.path.join(tmp, "graph.db"))
        # impl session: entities checkout + payments-api
        impl_checkout = store.upsert_node(kind="feature", name="checkout",
                                          session_id="s-impl", visibility="company")
        store.upsert_node(kind="service", name="payments-api",
                          session_id="s-impl", visibility="company")
        # blog session: entities checkout + blog (shares 'checkout' with impl)
        store.upsert_node(kind="feature", name="checkout",
                          session_id="s-blog", visibility="company")
        store.upsert_node(kind="feature", name="blog",
                          session_id="s-blog", visibility="company")
        # link entities that co-occur
        store.upsert_edge(src=impl_checkout, dst=store.node_ids_for_session("s-impl")[1],
                          rel="co_occurs", session_id="s-impl")

        # Seed from the blog session (the vector hit) — impl must be reachable.
        ranked = graph_search("launch announcement feature", store,
                              seed_session_ids=["s-blog"], limit=10)
        assert "s-impl" in ranked, ranked


def test_graph_search_uses_same_as_no_decay():
    """A same_as alias link should carry full weight across sessions."""
    with tempfile.TemporaryDirectory() as tmp:
        from contexthub.graph.store import GraphStore

        store = GraphStore(os.path.join(tmp, "graph.db"))
        a = store.upsert_node(kind="feature", name="checkout",
                              session_id="s-a", visibility="company")
        b = store.upsert_node(kind="feature", name="payment-checkout",
                              session_id="s-b", visibility="company")
        store.upsert_edge(src=a, dst=b, rel="same_as")

        ranked = graph_search("checkout", store, terms=["checkout"], limit=10)
        # Both sessions reachable; the same_as neighbor's session is included.
        assert "s-a" in ranked and "s-b" in ranked, ranked


def test_graph_search_empty_when_no_seeds():
    with tempfile.TemporaryDirectory() as tmp:
        from contexthub.graph.store import GraphStore

        store = GraphStore(os.path.join(tmp, "graph.db"))
        assert graph_search("nothing here", store, terms=["zzz"], limit=10) == []


# ---------------------------------------------------------------------------
# Generic-hub exclusion (GraphRAG overhaul)
# ---------------------------------------------------------------------------

def test_generic_nodes_do_not_seed_the_walk(tmp_path):
    from contexthub.graph.retrieve import graph_search
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    for i in range(30):
        store.upsert_node(kind="tool", name="python", session_id=f"s{i}")
    store.upsert_node(kind="feature", name="checkout", session_id="s1")
    store.recompute_generic_flags(fraction=0.25, min_total=20)

    # "python" is the only term match but it is generic → no seeds → no results.
    assert graph_search("python performance tips", store) == []
    # a non-generic concept still seeds normally
    assert "s1" in graph_search("checkout flow", store)


def test_generic_seed_sessions_do_not_flood_via_hub(tmp_path):
    from contexthub.graph.retrieve import graph_search
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    for i in range(30):
        store.upsert_node(kind="tool", name="python", session_id=f"s{i}")
    a = store.upsert_node(kind="feature", name="checkout", session_id="seed-sess")
    hub = store.upsert_node(kind="tool", name="python", session_id="seed-sess")
    store.upsert_edge(src=a, dst=hub, rel="uses", session_id="seed-sess")
    store.recompute_generic_flags(fraction=0.25, min_total=20)

    ranked = graph_search("unrelated words", store, seed_session_ids=["seed-sess"])
    # seed-sess surfaces via its non-generic entity; the 30 hub-only sessions
    # must not — the generic hub is neither seeded, expanded, nor projected.
    assert ranked == ["seed-sess"]
