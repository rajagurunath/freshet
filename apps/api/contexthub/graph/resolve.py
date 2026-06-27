"""Cross-session entity resolution (Slice 1).

Two separate AI-coding sessions often build their own little knowledge graphs and
refer to the *same* real-world concept under different names — one session names a
feature node "checkout", another names it "payment-checkout".  Left unlinked, the
two session graphs never connect, so a future question can't reach the older
session through the newer one.

This module links such nodes with a reversible ``same_as`` edge:

  1. blocking      — for each node a session upserted, gather same-KIND candidates
                     whose names share enough character n-grams (cheap, no LLM).
  2. scoring       — embed "{kind}: {name}. {summary}" for both nodes (MiniLM
                     vectors are L2-normalised, so cosine == dot product) and use
                     the cosine as the match score.
  3. decision      — score >= ``er_high_threshold`` ⇒ create a ``same_as`` edge;
                     score <  ``er_low_threshold``  ⇒ reject.  The grey band in
                     between is reserved for future LLM adjudication (TODO) and is
                     treated as a reject for this slice.

The ``same_as`` edge lives in the existing edges table (rel='same_as'), so it is
reversible (delete the row) and traversable by ``GraphStore.neighbors`` exactly
like any other edge.  ``get_canonical_id`` collapses a connected component of
``same_as`` edges to a single canonical id via an iterative BFS — no separate
node_aliases table is introduced.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Blocking threshold: candidates below this n-gram Jaccard are never scored.
_BLOCK_JACCARD = 0.4


def _normalize(name: str) -> str:
    """Lowercase a name and strip every non-alphanumeric character."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def ngram_jaccard(a: str, b: str, n: int = 3) -> float:
    """Character n-gram Jaccard similarity on normalised names.

    Names are lowercased and stripped of non-alphanumerics first.  Returns a
    value in [0, 1]; 1.0 means identical n-gram sets.  Short strings (shorter
    than ``n``) fall back to whole-string equality.
    """
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) < n or len(nb) < n:
        return 1.0 if na == nb else 0.0
    ga = {na[i : i + n] for i in range(len(na) - n + 1)}
    gb = {nb[i : i + n] for i in range(len(nb) - n + 1)}
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


def cosine(v1: list[float], v2: list[float]) -> float:
    """Cosine similarity of two vectors using only stdlib math.

    MiniLM embeddings are already L2-normalised, so the dot product alone is the
    cosine.  We still divide by the norms defensively in case a caller passes an
    un-normalised vector; a zero/non-finite norm yields 0.0.
    """
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = 0.0
    n1 = 0.0
    n2 = 0.0
    for a, b in zip(v1, v2):
        dot += a * b
        n1 += a * a
        n2 += b * b
    denom = math.sqrt(n1) * math.sqrt(n2)
    if not math.isfinite(denom) or denom == 0.0:
        return 0.0
    return dot / denom


def get_canonical_id(store: Any, node_id: str) -> str:
    """Return the canonical id of ``node_id``'s ``same_as`` connected component.

    Performs an iterative BFS over ``same_as`` edges read from the existing edges
    table (both directions, since ``same_as`` is symmetric) and returns the
    lexicographically smallest node id in the component — a stable canonical
    representative.  When the node has no ``same_as`` edges it is its own
    canonical id.
    """
    adj: dict[str, set[str]] = {}
    for src, dst in store.edges_by_rel("same_as"):
        adj.setdefault(src, set()).add(dst)
        adj.setdefault(dst, set()).add(src)

    seen = {node_id}
    frontier = [node_id]
    while frontier:
        nxt: list[str] = []
        for nid in frontier:
            for other in adj.get(nid, ()):
                if other not in seen:
                    seen.add(other)
                    nxt.append(other)
        frontier = nxt
    return min(seen)


def _embed_text(kind: str, name: str, summary: Optional[str]) -> str:
    """Build the canonical string embedded for entity-resolution scoring."""
    summary = (summary or "").strip()
    return f"{kind}: {name}. {summary}".strip()


def resolve_session_nodes(
    session_id: str,
    store: Any,
    embedder: Any,
    llm: Any = None,
    settings: Any = None,
    caller_user_id: Optional[str] = None,
    caller_team: Optional[str] = None,
) -> dict[str, int]:
    """Link a session's nodes to same-concept nodes elsewhere in the graph.

    For every node carrying ``session_id`` provenance, find same-KIND candidates
    in the graph (blocking on ``ngram_jaccard >= 0.4``), score the survivors with
    cosine over the MiniLM embedding of ``"{kind}: {name}. {summary}"`` and, when
    the score clears ``settings.er_high_threshold``, write a reversible
    ``same_as`` edge.  Scores below ``settings.er_low_threshold`` are rejected;
    the grey band in between is left for future LLM adjudication and counted as a
    reject for this slice.

    ``caller_user_id`` / ``caller_team`` scope the *candidate* pool: pass the
    resolving session's author and team so same-author private and same-team
    nodes are eligible targets (company nodes are always eligible). The session's
    own node rows are always fetched unfiltered so team/private sessions resolve.

    Returns ``{"same_as_added": int, "rejected": int}``.
    """
    high = float(getattr(settings, "er_high_threshold", 0.85))
    low = float(getattr(settings, "er_low_threshold", 0.55))

    # The nodes this session contributed. Fetched by id with no visibility filter
    # (resolution is an internal background pass) so team/private nodes are not
    # silently dropped, and with no 2000-row limit so large graphs stay complete.
    session_node_ids = set(store.node_ids_for_session(session_id))
    if not session_node_ids:
        return {"same_as_added": 0, "rejected": 0}
    session_nodes = store.get_nodes(session_node_ids)
    if not session_nodes:
        return {"same_as_added": 0, "rejected": 0}

    # Existing same_as links (unordered, unfiltered) — makes re-runs idempotent:
    # an already-linked pair is never re-counted or duplicated in reverse.
    linked: set[frozenset] = {
        frozenset((src, dst)) for src, dst in store.edges_by_rel("same_as")
    }

    # Group focal nodes by kind so each kind's candidate pool is fetched and
    # embedded exactly once, rather than re-scanning + re-embedding per node.
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for node in session_nodes:
        by_kind.setdefault(node["kind"], []).append(node)

    same_as_added = 0
    rejected = 0

    for kind, focal_nodes in by_kind.items():
        candidates = [
            c
            for c in store.get_nodes_by_kind(kind, caller_user_id, caller_team)
            # Skip the session's own nodes — concepts it deliberately kept apart.
            if c["id"] not in session_node_ids
        ]
        if not candidates:
            continue

        # Block first, then embed only the focal nodes + candidates that survive
        # blocking for at least one focal node (each embedded at most once).
        blocked_for: dict[str, list[dict[str, Any]]] = {}
        needed_cands: dict[str, dict[str, Any]] = {}
        for node in focal_nodes:
            survivors = []
            for cand in candidates:
                if cand["id"] == node["id"]:
                    continue
                if frozenset((node["id"], cand["id"])) in linked:
                    continue
                if ngram_jaccard(node["name"], cand["name"]) >= _BLOCK_JACCARD:
                    survivors.append(cand)
                    needed_cands[cand["id"]] = cand
            if survivors:
                blocked_for[node["id"]] = survivors
        if not needed_cands:
            continue

        # One embedding batch per kind, cached by node id.
        batch = list(focal_nodes) + list(needed_cands.values())
        texts = [_embed_text(n["kind"], n["name"], n.get("summary")) for n in batch]
        vecs = embedder.embed_texts(texts)
        if not vecs or len(vecs) != len(texts):
            continue
        vec_of = {n["id"]: v for n, v in zip(batch, vecs)}

        for node in focal_nodes:
            focal_vec = vec_of.get(node["id"])
            if focal_vec is None:
                continue
            for cand in blocked_for.get(node["id"], ()):  # already pre-blocked
                pair = frozenset((node["id"], cand["id"]))
                if pair in linked:
                    continue
                cand_vec = vec_of.get(cand["id"])
                if cand_vec is None:
                    continue
                score = cosine(focal_vec, cand_vec)
                if score >= high:
                    # Reversible same_as edge; tagged with session provenance.
                    store.upsert_edge(
                        src=node["id"], dst=cand["id"], rel="same_as",
                        session_id=session_id,
                    )
                    linked.add(pair)  # guard against a reverse dup within this run
                    same_as_added += 1
                    logger.info(
                        "entity_resolve: linked %s '%s' <-same_as-> '%s' (score %.3f)",
                        kind, node["name"], cand["name"], score,
                    )
                elif score < low:
                    rejected += 1
                else:
                    # TODO(slice-2): LLM adjudication for the grey band
                    # [er_low_threshold, er_high_threshold); reject for now.
                    rejected += 1

    return {"same_as_added": same_as_added, "rejected": rejected}
