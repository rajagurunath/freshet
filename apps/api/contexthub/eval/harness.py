"""Ingest the synthetic corpus into isolated temp stores and score retrievers.

This is the rig that A/Bs retrieval strategies. It deliberately does NOT touch
the user's real ``./data`` — everything lives in a throwaway temp directory and a
fresh ``VectorStore`` / ``GraphStore`` so a benchmark run can never corrupt real
sessions or the live graph.

The graph is populated deterministically from the corpus's *planted* entities
(no LLM needed): each session's entities become nodes with session provenance and
the entities that co-occur in a session are linked. That is exactly the structure
an NER/LLM extractor would produce — made deterministic so the benchmark is
reproducible and offline.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Callable, Optional

from contexthub.embeddings import get_embedder
from contexthub.eval.corpus import Corpus, build_corpus
from contexthub.ingest.chunker import build_chunks
from contexthub.storage.vectors import VectorStore


def _dedup_keep_order(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


@dataclass
class EvalEnv:
    """A fully-ingested, isolated evaluation environment."""

    corpus: Corpus
    vectors: VectorStore
    embedder: object
    graph: Optional[object]  # GraphStore | None
    _tmpdir: str

    def close(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def __enter__(self) -> "EvalEnv":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- retrievers ------------------------------------------------------

    def baseline_retriever(self, mode: str = "hybrid") -> Callable[[str, int], list[str]]:
        """Vanilla vector+FTS retriever → ranked, de-duplicated session ids.

        This is the baseline every other slice must beat.
        """
        def retrieve(question: str, k: int) -> list[str]:
            qvec = self.embedder.embed_query(question)
            # Oversample chunks so we can dedup to k distinct sessions.
            rows = self.vectors.hybrid_search(
                query=question, query_vec=qvec, top_k=k * 4, mode=mode,
            )
            return _dedup_keep_order([r.get("session_id", "") for r in rows])[:k]
        return retrieve

    def graph_retriever(self) -> Callable[[str, int], list[str]]:
        """Vector + FTS + graph-arm retriever (3-arm RRF). Requires ``with_graph``."""
        if self.graph is None:
            raise ValueError("graph_retriever requires build_env(with_graph=True)")
        from contexthub.graph.retrieve import graph_fused_search

        def retrieve(question: str, k: int) -> list[str]:
            return graph_fused_search(
                question, self.vectors, self.embedder, self.graph, top_k=k,
            )
        return retrieve


def _populate_graph(graph, corpus: Corpus) -> None:
    """Deterministically build the knowledge graph from planted entities.

    Mirrors what extract.py + resolve.py would produce, minus the LLM: an entity
    node per planted name (deduped by (kind,name)), session provenance, and an
    edge between entities that co-occur in a session.
    """
    for it in corpus.items:
        ids: list[str] = []
        for name in it.entities:
            # Heuristic kind: anything ending in -service/-api is a service.
            kind = "service" if name.endswith(("-service", "-api")) else "feature"
            nid = graph.upsert_node(
                kind=kind, name=name, session_id=it.session.id,
                visibility="company", summary=it.summary[:80],
            )
            ids.append(nid)
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                graph.upsert_edge(src=ids[a], dst=ids[b], rel="co_occurs",
                                  session_id=it.session.id)


def _populate_graph_ner(graph, corpus: Corpus) -> None:
    """Build the graph by running the REAL NER pipeline on each session's text.

    Unlike ``_populate_graph`` (oracle planted entities), this measures what the
    shipping deterministic extractor actually recovers — structural entities like
    services and libraries. It does NOT recover bare feature concepts ("checkout"),
    which need the LLM extractor or GLiNER; that gap is the point of measuring it.
    """
    from contexthub.graph.ner import extract_ner_graph

    for it in corpus.items:
        extract_ner_graph(it.session, it.summary, graph, visibility="company")


def build_env(
    embedder_provider: str = "local",
    body_chars: int = 2400,
    with_graph: bool = False,
    graph_source: str = "planted",
) -> EvalEnv:
    """Build and ingest an isolated evaluation environment.

    Args:
        embedder_provider: "local" (real MiniLM — for meaningful numbers) or
            "hash" (offline, deterministic — for plumbing tests; not semantic).
        body_chars: transcript length per session.
        with_graph: also build a GraphStore.
        graph_source: "planted" (oracle entities — represents LLM+NER combined) or
            "ner" (run the real deterministic NER pipeline — honest lower bound).
    """
    tmpdir = tempfile.mkdtemp(prefix="ctxhub-eval-")
    corpus = build_corpus(body_chars=body_chars)

    embedder = get_embedder(embedder_provider)
    vectors = VectorStore(uri=f"{tmpdir}/lancedb", embedding_dim=embedder.dim)

    for it in corpus.items:
        chunks = build_chunks(it.session, summary=it.summary)
        texts = [c.text for c in chunks]
        vecs = embedder.embed_texts(texts) if texts else []
        rows = [
            {
                "id": c.id, "session_id": it.session.id, "tool": it.session.tool,
                "category": "engineering", "author": "eval", "team": it.team,
                "project": it.session.project or "", "visibility": "company",
                "text": c.text, "vector": v, "created_at": "2026-01-01T00:00:00Z",
            }
            for c, v in zip(chunks, vecs)
        ]
        vectors.upsert_chunks(rows)
        vectors.upsert_session({
            "id": it.session.id, "tool": it.session.tool, "title": it.session.title,
            "category": "engineering", "author": "eval", "team": it.team,
            "project": it.session.project or "", "visibility": "company",
            "message_count": it.session.message_count, "models": ["synthetic"],
            "preview": it.session.preview, "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z", "blob_uri": "", "summary": it.summary,
            "content_hash": it.session.id, "tokens_input": 0, "tokens_output": 0,
            "tokens_total": 0,
        })
    vectors.ensure_fts_index()

    graph = None
    if with_graph:
        from contexthub.graph.store import GraphStore
        graph = GraphStore(f"{tmpdir}/graph.db")
        if graph_source == "ner":
            _populate_graph_ner(graph, corpus)
        else:
            _populate_graph(graph, corpus)

    return EvalEnv(corpus=corpus, vectors=vectors, embedder=embedder,
                   graph=graph, _tmpdir=tmpdir)
