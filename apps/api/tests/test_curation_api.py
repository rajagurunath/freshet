"""HTTP surface for graph curation (curation API endpoints)."""

from __future__ import annotations

import os
import sys
from typing import Generator

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer alice-key"}


def _clear_caches() -> None:
    mods = [
        "contexthub.config", "contexthub.embeddings", "contexthub.storage.blob",
        "contexthub.storage.vectors", "contexthub.jobs.store", "contexthub.graph.store",
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


@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": str(tmp_path / "lancedb"),
        "BLOB_DIR": str(tmp_path / "blobs"),
        "JOBS_DB": str(tmp_path / "jobs.db"),
        "GRAPH_DB": str(tmp_path / "graph.db"),
        "API_KEYS": "alice-key:alice:team-red",
        "ANTHROPIC_API_KEY": "",
        "LLM_PROVIDER": "anthropic",
        "S3_BUCKET": "",
        "CORS_ORIGINS": "",
    }
    original = {k: os.environ.get(k) for k in env_patch}
    os.environ.update(env_patch)
    _clear_caches()
    from contexthub.main import create_app

    with TestClient(create_app(), raise_server_exceptions=True) as c:
        yield c
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _clear_caches()


def _mk_node(kind="feature", name="payment-checkout"):
    from contexthub.graph.store import get_graph_store
    return get_graph_store().upsert_node(kind=kind, name=name, session_id="s1")


def test_patch_rename(client):
    nid = _mk_node()
    r = client.patch(f"/v1/graph/nodes/{nid}", json={"name": "checkout"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == nid and body["merged"] is False
    assert body["node"]["name"] == "checkout"


def test_patch_rename_merges_onto_existing(client):
    a = _mk_node(name="payment-checkout")
    b = _mk_node(name="checkout")
    r = client.patch(f"/v1/graph/nodes/{a}", json={"name": "checkout"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == b and body["merged"] is True


def test_patch_missing_node_404(client):
    r = client.patch("/v1/graph/nodes/nope", json={"name": "x"}, headers=AUTH)
    assert r.status_code == 404


def test_delete_node_then_create_conflicts(client):
    nid = _mk_node(kind="service", name="turn-your-api")
    r = client.delete(f"/v1/graph/nodes/{nid}", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    r = client.post("/v1/graph/nodes",
                    json={"kind": "service", "name": "turn-your-api"}, headers=AUTH)
    assert r.status_code == 409


def test_create_node_and_edge_and_delete_edge(client):
    r = client.post("/v1/graph/nodes",
                    json={"kind": "decision", "name": "sqlite over kuzudb"}, headers=AUTH)
    assert r.status_code == 201
    a = r.json()["id"]
    b = _mk_node(kind="feature", name="graph store")
    r = client.post("/v1/graph/edges", json={"src": a, "dst": b, "rel": "decided"}, headers=AUTH)
    assert r.status_code == 201
    eid = r.json()["id"]
    r = client.delete(f"/v1/graph/edges/{eid}", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    r = client.post("/v1/graph/edges", json={"src": a, "dst": "missing", "rel": "uses"}, headers=AUTH)
    assert r.status_code == 404
