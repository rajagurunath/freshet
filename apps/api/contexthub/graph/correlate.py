"""Cross-session correlation signals *beyond* the entity graph.

The entity knowledge graph is one lens on "which sessions relate". The user
asked what else there is. This module adds two signals on different axes:

1. **kNN session-similarity edges** (``session_similarity_edges``) — a graph of
   *semantic* similarity between whole sessions (mutual-kNN over summary
   embeddings). This is the most honest "correlation beyond the entity graph":
   it links sessions that feel similar even when they share no extracted entity.
   Its primary value is **navigation** ("show me sessions like this one"), not a
   retrieval boost — it is a re-view of the embedding space the vector arm already
   searches, so it adds browsing UX, not new recall (consistent with the research).

2. **PPMI entity co-occurrence** (``ppmi_entity_pairs``) — a *statistical* signal
   orthogonal to embeddings. Positive pointwise mutual information surfaces entity
   pairs that co-occur across sessions far more than chance (a niche library that
   always shows up with one specific service), which cosine-of-summaries smooths
   away. Useful to **weight graph edges** and to explain *why* two sessions relate.

Both use only numpy/stdlib — no new dependencies, fully offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class SessionEdge:
    src: str
    dst: str
    weight: float  # cosine similarity in [−1, 1]


@dataclass(frozen=True)
class EntityPair:
    a: str            # "kind:name"
    b: str
    ppmi: float
    cooccur: int      # number of sessions both appear in


def _cosine_matrix(vectors: list[list[float]]) -> list[list[float]]:
    """Pairwise cosine similarity. Pure stdlib (small N: tens–thousands)."""
    norms = []
    for v in vectors:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        norms.append(n)
    out: list[list[float]] = []
    for i, vi in enumerate(vectors):
        row = []
        for j, vj in enumerate(vectors):
            if i == j:
                row.append(1.0)
                continue
            dot = sum(a * b for a, b in zip(vi, vj))
            row.append(dot / (norms[i] * norms[j]))
        out.append(row)
    return out


def session_similarity_edges(
    session_ids: list[str],
    vectors: list[list[float]],
    k: int = 5,
    min_sim: float = 0.3,
    mutual: bool = True,
) -> list[SessionEdge]:
    """Build "related sessions" edges via (mutual) kNN over session embeddings.

    Args:
        session_ids: ids aligned with ``vectors``.
        vectors: one embedding per session (e.g. of the summary).
        k: neighbours per session.
        min_sim: cosine floor — drop weak links so sparse regions don't get
            spurious neighbours.
        mutual: keep an edge only if each session is in the other's top-k
            (sharply reduces hub/one-sided noise).
    """
    n = len(session_ids)
    if n < 2:
        return []
    sim = _cosine_matrix(vectors)

    # top-k neighbour set per node (above the floor)
    topk: list[set[int]] = []
    for i in range(n):
        order = sorted(
            (j for j in range(n) if j != i and sim[i][j] >= min_sim),
            key=lambda j: sim[i][j], reverse=True,
        )[:k]
        topk.append(set(order))

    edges: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in topk[i]:
            if mutual and i not in topk[j]:
                continue
            a, b = (i, j) if i < j else (j, i)
            edges[(a, b)] = sim[a][b]

    return [
        SessionEdge(src=session_ids[a], dst=session_ids[b], weight=round(w, 4))
        for (a, b), w in sorted(edges.items(), key=lambda kv: kv[1], reverse=True)
    ]


def related_sessions(
    target_id: str,
    session_ids: list[str],
    vectors: list[list[float]],
    k: int = 5,
    min_sim: float = 0.3,
) -> list[tuple[str, float]]:
    """Top-k sessions most similar to ``target_id`` (for a 'related' panel)."""
    if target_id not in session_ids:
        return []
    idx = session_ids.index(target_id)
    sim = _cosine_matrix(vectors)
    scored = [
        (session_ids[j], sim[idx][j])
        for j in range(len(session_ids))
        if j != idx and sim[idx][j] >= min_sim
    ]
    scored.sort(key=lambda t: t[1], reverse=True)
    return [(sid, round(s, 4)) for sid, s in scored[:k]]


def ppmi_entity_pairs(
    store: Any,
    min_cooccur: int = 2,
    caller_user_id: Optional[str] = None,
    caller_team: Optional[str] = None,
) -> list[EntityPair]:
    """Rank entity pairs by Positive PMI over the session×entity incidence.

    ``PMI(x,y) = log( P(x,y) / (P(x)·P(y)) )``, clipped at 0. A high PPMI pair
    co-occurs far more than independence predicts — a signal cosine similarity of
    summaries does not capture. The ``min_cooccur`` floor guards against PMI's
    well-known overweighting of rare pairs.
    """
    try:
        nodes = store.list_nodes(caller_user_id=caller_user_id, caller_team=caller_team)
    except TypeError:
        nodes = store.list_nodes()
    # entity key -> set of sessions
    ent_sessions: dict[str, set[str]] = {}
    all_sessions: set[str] = set()
    for n in nodes:
        key = f"{n['kind']}:{n['name']}"
        try:
            sids = set(store.sessions_for_node(n["id"]))
        except Exception:
            sids = set()
        if not sids:
            continue
        ent_sessions.setdefault(key, set()).update(sids)
        all_sessions.update(sids)

    total = len(all_sessions)
    if total < 2:
        return []

    keys = sorted(ent_sessions)
    pairs: list[EntityPair] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            sa, sb = ent_sessions[a], ent_sessions[b]
            both = sa & sb
            c = len(both)
            if c < min_cooccur:
                continue
            p_a = len(sa) / total
            p_b = len(sb) / total
            p_ab = c / total
            denom = p_a * p_b
            if denom <= 0:
                continue
            pmi = math.log(p_ab / denom)
            if pmi <= 0:
                continue
            pairs.append(EntityPair(a=a, b=b, ppmi=round(pmi, 4), cooccur=c))

    pairs.sort(key=lambda p: p.ppmi, reverse=True)
    return pairs


def refresh_cooccur_edges(
    store: Any,
    min_cooccur: int = 2,
    max_pairs: int = 500,
) -> int:
    """Rebuild ``co_occurs`` edges from corpus-level PPMI.

    Replaces the old per-session star topology (hub = first-matched entity,
    semantically void) with edges only between statistically associated pairs.
    Idempotent: deletes all existing ``co_occurs`` edges first. PPMI itself
    down-weights ubiquitous entities, so generic hubs rarely earn an edge.
    Returns the number of edges written.
    """
    pairs = ppmi_entity_pairs(store, min_cooccur=min_cooccur)
    nodes = store.list_nodes(limit=100_000)
    by_key = {f"{n['kind']}:{n['name']}": n["id"] for n in nodes}
    store.delete_edges_by_rel("co_occurs")
    written = 0
    for p in pairs[:max_pairs]:
        a, b = by_key.get(p.a), by_key.get(p.b)
        if not a or not b:
            continue
        try:
            store.upsert_edge(src=a, dst=b, rel="co_occurs", weight=round(p.ppmi, 4))
            written += 1
        except ValueError:
            continue
    return written
