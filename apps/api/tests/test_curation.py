"""Curation memory: human edits always beat machine re-extraction."""

import pytest

from contexthub.graph.store import GraphStore


@pytest.fixture
def store(tmp_path):
    return GraphStore(str(tmp_path / "graph.db"))


def test_deleted_node_is_not_resurrected_by_extraction(store):
    nid = store.upsert_node(kind="service", name="turn-your-api", session_id="s1")
    store.delete_node(nid)
    assert store.list_nodes() == []
    with pytest.raises(ValueError):
        store.upsert_node(kind="service", name="turn-your-api", session_id="s2")
    assert store.list_nodes() == []


def test_rename_creates_alias_that_remaps_future_extractions(store):
    nid = store.upsert_node(kind="feature", name="payment-checkout", session_id="s1")
    res = store.rename_node(nid, "checkout")
    assert res == {"id": nid, "merged": False}
    # machine re-extraction of the OLD name lands on the renamed node
    assert store.upsert_node(kind="feature", name="payment-checkout", session_id="s2") == nid
    assert set(store.sessions_for_node(nid)) == {"s1", "s2"}
    assert [n["name"] for n in store.list_nodes()] == ["checkout"]


def test_rename_onto_existing_node_hard_merges(store):
    a = store.upsert_node(kind="feature", name="payment-checkout", session_id="s1")
    b = store.upsert_node(kind="feature", name="checkout", session_id="s2")
    tool = store.upsert_node(kind="tool", name="stripe", session_id="s1")
    store.upsert_edge(src=a, dst=tool, rel="uses", session_id="s1")
    store.upsert_edge(src=b, dst=tool, rel="uses", session_id="s2")

    res = store.rename_node(a, "checkout")
    assert res == {"id": b, "merged": True}
    assert {n["name"] for n in store.list_nodes()} == {"checkout", "stripe"}
    # provenance moved
    assert set(store.sessions_for_node(b)) == {"s1", "s2"}
    # duplicate edges collapsed, weights accumulated
    uses = [e for e in store.list_edges() if e["rel"] == "uses"]
    assert len(uses) == 1
    assert uses[0]["weight"] == 2.0
    # the old name now aliases to the survivor
    assert store.upsert_node(kind="feature", name="payment-checkout", session_id="s3") == b


def test_human_edited_fields_survive_machine_upsert(store):
    nid = store.upsert_node(kind="feature", name="checkout", session_id="s1", summary="machine")
    store.update_node(nid, summary="the checkout flow (human)")
    store.upsert_node(kind="feature", name="checkout", session_id="s2", summary="machine again")
    node = store.get_nodes([nid])[0]
    assert node["summary"] == "the checkout flow (human)"
    assert set(store.sessions_for_node(nid)) == {"s1", "s2"}  # provenance still accumulates


def test_update_node_kind_collision_raises(store):
    a = store.upsert_node(kind="feature", name="checkout", session_id="s1")
    store.upsert_node(kind="decision", name="checkout", session_id="s2")
    with pytest.raises(ValueError):
        store.update_node(a, kind="decision")


def test_deleted_edge_is_not_rewritten(store):
    a = store.upsert_node(kind="feature", name="checkout", session_id="s1")
    b = store.upsert_node(kind="tool", name="stripe", session_id="s1")
    eid = store.upsert_edge(src=a, dst=b, rel="uses")
    store.delete_edge(eid)
    with pytest.raises(ValueError):
        store.upsert_edge(src=a, dst=b, rel="uses")
    assert store.list_edges() == []


def test_manual_add_of_tombstoned_name_conflicts(store):
    nid = store.upsert_node(kind="tool", name="foo", session_id="s1")
    store.delete_node(nid)
    with pytest.raises(ValueError):
        store.create_node(kind="tool", name="foo")


def test_create_node_and_edge_by_hand(store):
    node = store.create_node(kind="decision", name="sqlite over kuzudb", summary="supply-chain risk")
    other = store.create_node(kind="feature", name="graph store")
    eid = store.create_edge(src=node["id"], dst=other["id"], rel="decided")
    assert eid
    with pytest.raises(KeyError):
        store.create_edge(src=node["id"], dst="missing-id", rel="uses")
    # human-created summary is protected from machine overwrite
    store.upsert_node(kind="decision", name="sqlite over kuzudb", session_id="s9", summary="machine")
    assert store.get_nodes([node["id"]])[0]["summary"] == "supply-chain risk"
