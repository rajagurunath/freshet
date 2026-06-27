"""Graph retrieval arm — turn the knowledge graph into a session ranking.

This is the heart of the GraphRAG moat. Plain vector/FTS retrieval fails the
*bridge* case: a question whose answer lives in a session that shares none of the
question's vocabulary, and is only reachable through a shared entity. The graph
arm fixes that with **entity-seeded expansion**:

1. **Seed** the graph from the query two ways:
   - query terms → matching node names (``find_nodes_by_terms``);
   - the top vector hits' sessions → their entity nodes (``node_ids_for_session``).
     This second path is what cracks the bridge case: the vector hit that *does*
     match the query (the blog post) drags in its entities (``checkout``), whose
     other sessions (the implementation) then surface.
2. **Expand** out 1–2 hops over edges with hop decay, treating ``same_as`` links
   as no-decay (a resolved alias is the *same* concept, not a weaker neighbor).
3. **Project** the scored nodes back onto sessions via provenance and rank.

``graph_fused_search`` then fuses the graph ranking with the vanilla vector and
FTS arms via the existing RRF, so the graph can only *help* — a noisy graph arm
is outvoted, a useful one promotes the right session. Visibility is enforced
throughout by delegating to ``GraphStore`` (which applies the caller's scope).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Optional

# Minimal stopword list for pulling candidate entity terms from a question.
_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "is", "are",
    "what", "how", "why", "when", "where", "who", "which", "tell", "me", "about",
    "do", "does", "did", "was", "were", "be", "with", "this", "that", "it", "we",
    "our", "us", "they", "them", "their", "i", "you", "your", "can", "could",
    "would", "should", "get", "got", "use", "used", "using", "make", "made",
    "give", "full", "story", "across", "every", "all", "into", "over", "out",
}

_SAME_AS = "same_as"


def question_terms(question: str) -> list[str]:
    """Candidate entity terms from a question (drop stopwords / short tokens)."""
    tokens = re.findall(r"[a-zA-Z0-9_\-]+", (question or "").lower())
    return [t for t in tokens if len(t) >= 3 and t not in _STOPWORDS]


def graph_search(
    question: str,
    store: Any,
    seed_session_ids: Optional[Sequence[str]] = None,
    terms: Optional[Sequence[str]] = None,
    extra_seed_node_ids: Optional[Sequence[str]] = None,
    caller_user_id: Optional[str] = None,
    caller_team: Optional[str] = None,
    max_hops: int = 2,
    decay: float = 0.5,
    limit: int = 20,
) -> list[str]:
    """Rank sessions by graph proximity to the query. Returns ranked session ids.

    Args:
        seed_session_ids: sessions from a prior vector pass whose entities seed the
            walk (this is what enables bridge retrieval). Optional.
        extra_seed_node_ids: node ids to seed from directly (e.g. node-vector hits).
        max_hops / decay: BFS depth and per-hop score decay (``same_as`` = no decay).
        limit: max sessions returned.
    """
    # --- 1. collect seed node ids -------------------------------------------
    seeds: dict[str, float] = {}

    term_list = list(terms) if terms is not None else question_terms(question)
    if term_list:
        try:
            for n in store.find_nodes_by_terms(
                term_list, caller_user_id=caller_user_id,
                caller_team=caller_team, limit=30,
            ):
                seeds[n["id"]] = max(seeds.get(n["id"], 0.0), 1.0)
        except Exception:
            pass

    for sid in seed_session_ids or []:
        try:
            for nid in store.node_ids_for_session(sid):
                seeds[nid] = max(seeds.get(nid, 0.0), 1.0)
        except Exception:
            continue

    for nid in extra_seed_node_ids or []:
        seeds[nid] = max(seeds.get(nid, 0.0), 1.0)

    if not seeds:
        return []

    # --- 2. BFS expansion with hop decay (same_as = no decay) ---------------
    node_score: dict[str, float] = {}
    frontier: dict[str, float] = dict(seeds)
    for hop in range(max_hops + 1):
        next_frontier: dict[str, float] = {}
        for nid, w in frontier.items():
            if w <= node_score.get(nid, 0.0):
                # Already reached this node via an equal/stronger path.
                if nid in node_score:
                    continue
            node_score[nid] = max(node_score.get(nid, 0.0), w)
            if hop >= max_hops:
                continue
            try:
                sub = store.neighbors(
                    nid, depth=1, caller_user_id=caller_user_id,
                    caller_team=caller_team,
                )
            except Exception:
                continue
            for e in sub.get("edges", []):
                src, dst = e.get("src"), e.get("dst")
                other = dst if src == nid else (src if dst == nid else None)
                if not other:
                    continue
                step = 1.0 if e.get("rel") == _SAME_AS else decay
                nw = w * step
                if nw > next_frontier.get(other, 0.0):
                    next_frontier[other] = nw
        if not next_frontier:
            break
        frontier = next_frontier

    # --- 3. project nodes onto sessions -------------------------------------
    sess_score: dict[str, float] = {}
    for nid, w in node_score.items():
        try:
            for sid in store.sessions_for_node(nid):
                sess_score[sid] = sess_score.get(sid, 0.0) + w
        except Exception:
            continue

    ranked = sorted(sess_score, key=lambda s: sess_score[s], reverse=True)
    return ranked[:limit]


def _dedup(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def weighted_rrf(
    rank_lists: Sequence[Sequence[str]],
    weights: Sequence[float],
    k: int = 60,
) -> list[str]:
    """Reciprocal Rank Fusion with a per-list weight.

    ``score(d) = Σ_i weight_i / (k + rank_i(d) + 1)``. With equal weights this is
    identical to the plain ``rrf``. We give the graph arm a modest boost so a
    strong graph-only candidate (a bridge answer the lexical/semantic arms never
    surface) can still reach the top — without letting it override consensus on
    the easy queries, where vector+FTS agreement outweighs it.
    """
    scores: dict[str, float] = {}
    for ranks, w in zip(rank_lists, weights):
        for i, rid in enumerate(ranks):
            scores[rid] = scores.get(rid, 0.0) + w / (k + i + 1)
    return sorted(scores, key=lambda x: scores[x], reverse=True)


def graph_fused_search(
    question: str,
    vectors: Any,
    embedder: Any,
    store: Any,
    top_k: int = 10,
    filters: Optional[dict[str, Any]] = None,
    caller_user_id: Optional[str] = None,
    caller_team: Optional[str] = None,
    graph_seed_k: int = 8,
    graph_weight: float = 0.6,
    candidate_k: Optional[int] = None,
) -> list[str]:
    """Vector + FTS + graph, fused with weighted RRF → ranked session ids.

    The graph arm is seeded from the top ``graph_seed_k`` vector-hit sessions
    (plus query terms), so it adds bridge/alias candidates the lexical/semantic
    arms miss. ``graph_weight`` gives that arm a modest boost (tuned on the eval
    harness) so a graph-only bridge answer can reach the top without overriding
    vector+FTS consensus on easy queries.
    """
    cand = candidate_k or max(top_k * 4, 20)
    qvec = embedder.embed_query(question)

    def _sessions(mode: str) -> list[str]:
        rows = vectors.hybrid_search(
            query=question, query_vec=qvec, top_k=cand, mode=mode,
            filters=filters, caller_user_id=caller_user_id, caller_team=caller_team,
        )
        return _dedup([r.get("session_id", "") for r in rows])

    vec_sessions = _sessions("vector")
    fts_sessions = _sessions("keyword")

    graph_sessions = graph_search(
        question, store,
        seed_session_ids=vec_sessions[:graph_seed_k],
        caller_user_id=caller_user_id, caller_team=caller_team,
        limit=cand,
    )

    fused = weighted_rrf(
        [vec_sessions, fts_sessions, graph_sessions],
        weights=[1.0, 1.0, graph_weight], k=60,
    )
    return fused[:top_k]
