"""Tests for graph-derived cross-session insights (Slice S8)."""

from __future__ import annotations

import os
import tempfile

from contexthub.graph.insights import common_pairings, stack_profile


def _store(tmp):
    from contexthub.graph.store import GraphStore
    return GraphStore(os.path.join(tmp, "graph.db"))


def test_stack_profile_ranks_recurring_tools():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        # redis used in 3 sessions, postgres in 2, sqlite in 1.
        for sid in ("s1", "s2", "s3"):
            store.upsert_node(kind="tool", name="redis", session_id=sid, visibility="company")
        for sid in ("s1", "s2"):
            store.upsert_node(kind="tool", name="postgres", session_id=sid, visibility="company")
        store.upsert_node(kind="tool", name="sqlite", session_id="s1", visibility="company")

        profile = stack_profile(store, min_sessions=2)
        names = [e.name for e in profile]
        assert names[0] == "redis"  # most sessions first
        assert "postgres" in names
        assert "sqlite" not in names  # below min_sessions floor


def test_stack_profile_filters_by_kind():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        for sid in ("s1", "s2"):
            store.upsert_node(kind="tool", name="redis", session_id=sid, visibility="company")
            store.upsert_node(kind="feature", name="checkout", session_id=sid, visibility="company")
        profile = stack_profile(store, kinds={"tool"}, min_sessions=2)
        kinds = {e.kind for e in profile}
        assert kinds == {"tool"}  # features excluded


def test_common_pairings_surfaces_cooccurrence():
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        for sid in ("s1", "s2"):
            store.upsert_node(kind="service", name="payments-api", session_id=sid, visibility="company")
            store.upsert_node(kind="tool", name="stripe", session_id=sid, visibility="company")
        store.upsert_node(kind="tool", name="redis", session_id="s3", visibility="company")
        store.upsert_node(kind="tool", name="redis", session_id="s4", visibility="company")

        pairs = common_pairings(store, min_cooccur=2)
        keys = {(p["a"], p["b"]) for p in pairs}
        assert ("service:payments-api", "tool:stripe") in keys or \
               ("tool:stripe", "service:payments-api") in keys
        assert all(p["cooccur"] >= 2 for p in pairs)
