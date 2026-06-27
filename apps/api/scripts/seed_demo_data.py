"""Seed a populated demo dataset for the UI — isolated from real ./data.

Writes the synthetic eval corpus (sessions + chunks) into a LanceDB at
``$LANCEDB_URI`` and a knowledge graph (planted entities + co-occurrence +
a couple of same_as alias links) into ``$GRAPH_DB``, using the real local
MiniLM embedder so live queries work. No LLM, no network.

Run with the demo paths so the running API picks them up, e.g.:
    LANCEDB_URI=./data-demo/lancedb GRAPH_DB=./data-demo/graph.db \
    BLOB_DIR=./data-demo/blobs JOBS_DB=./data-demo/jobs.db \
    python -m scripts.seed_demo_data
"""

from __future__ import annotations

import os

from contexthub.embeddings import get_embedder
from contexthub.eval.corpus import build_corpus
from contexthub.graph.store import GraphStore
from contexthub.ingest.chunker import build_chunks
from contexthub.storage.vectors import VectorStore


def main() -> None:
    lancedb_uri = os.environ.get("LANCEDB_URI", "./data-demo/lancedb")
    graph_db = os.environ.get("GRAPH_DB", "./data-demo/graph.db")
    os.makedirs(os.path.dirname(graph_db) or ".", exist_ok=True)

    corpus = build_corpus()
    embedder = get_embedder("local")
    vectors = VectorStore(uri=lancedb_uri, embedding_dim=embedder.dim)
    graph = GraphStore(graph_db)

    for it in corpus.items:
        chunks = build_chunks(it.session, summary=it.summary)
        texts = [c.text for c in chunks]
        vecs = embedder.embed_texts(texts) if texts else []
        vectors.upsert_chunks([
            {
                "id": c.id, "session_id": it.session.id, "tool": it.session.tool,
                "category": "engineering", "author": "demo", "team": it.team,
                "project": it.session.project or "", "visibility": "company",
                "text": c.text, "vector": v, "created_at": "2026-06-27T00:00:00Z",
            }
            for c, v in zip(chunks, vecs)
        ])
        vectors.upsert_session({
            "id": it.session.id, "tool": it.session.tool, "title": it.session.title,
            "category": "engineering", "author": "demo", "team": it.team,
            "project": it.session.project or "", "visibility": "company",
            "message_count": it.session.message_count, "models": ["synthetic"],
            "preview": it.summary, "created_at": "2026-06-27T00:00:00Z",
            "updated_at": "2026-06-27T00:00:00Z", "blob_uri": "", "summary": it.summary,
            "content_hash": it.session.id, "tokens_input": 0, "tokens_output": 0,
            "tokens_total": 0, "graph_extracted": True, "links_json": "[]",
        })

        # graph: planted entities (deduped by kind,name) + co-occurrence edges
        ids = []
        for name in it.entities:
            kind = "service" if name.endswith(("-service", "-api")) else "feature"
            ids.append(graph.upsert_node(kind=kind, name=name, session_id=it.session.id,
                                         visibility="company", summary=it.summary[:80]))
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                graph.upsert_edge(src=ids[a], dst=ids[b], rel="co_occurs",
                                  session_id=it.session.id)
    vectors.ensure_fts_index()

    # A couple of same_as alias links so the graph viz shows cross-session bridges.
    names = {n["name"]: n["id"] for n in graph.list_nodes()}
    # (no synthetic aliases needed here; planted shared names already link sessions)

    n_sessions = len(corpus.items)
    n_nodes = len(graph.list_nodes())
    print(f"seeded {n_sessions} sessions, {n_nodes} graph nodes -> {lancedb_uri}, {graph_db}")


if __name__ == "__main__":
    main()
