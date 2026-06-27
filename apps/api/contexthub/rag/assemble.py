"""Context assembly — diversify the final retrieved set with MMR.

After fusion + reranking you often have several near-duplicate chunks (e.g. three
slices of the same long session) competing for a limited token budget. Maximal
Marginal Relevance (MMR) greedily picks items that are relevant to the query *and*
dissimilar to what's already chosen:

    MMR = argmax_d [ λ·sim(d, q) − (1−λ)·max_{d' in chosen} sim(d, d') ]

so the assembled context covers more distinct sessions/aspects rather than
restating one. Pure numpy/stdlib, fully offline. λ≈0.7 leans toward relevance.
"""

from __future__ import annotations

import math
from typing import Any, Callable, Optional, Sequence


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def mmr_select(
    query_vec: Sequence[float],
    candidates: list[dict[str, Any]],
    vector_key: str = "vector",
    k: int = 8,
    lambda_param: float = 0.7,
) -> list[dict[str, Any]]:
    """Select up to ``k`` candidates by MMR (relevance + diversity).

    Each candidate must carry an embedding under ``vector_key``. Candidates
    without a usable vector are appended (in original order) after the MMR
    selection so nothing is silently dropped. Returns the selected dicts in pick
    order. With ``lambda_param=1.0`` this is pure relevance ordering.
    """
    pool = [c for c in candidates if c.get(vector_key)]
    no_vec = [c for c in candidates if not c.get(vector_key)]
    if not pool:
        return candidates[:k]

    rel = {id(c): _cos(query_vec, c[vector_key]) for c in pool}
    chosen: list[dict[str, Any]] = []
    remaining = list(pool)

    while remaining and len(chosen) < k:
        best = None
        best_score = -math.inf
        for c in remaining:
            if not chosen:
                score = rel[id(c)]
            else:
                max_sim = max(_cos(c[vector_key], s[vector_key]) for s in chosen)
                score = lambda_param * rel[id(c)] - (1.0 - lambda_param) * max_sim
            if score > best_score:
                best_score = score
                best = c
        chosen.append(best)
        remaining.remove(best)

    if len(chosen) < k:
        chosen.extend(no_vec[: k - len(chosen)])
    return chosen


def assemble_context(
    query_vec: Sequence[float],
    candidates: list[dict[str, Any]],
    embed_fn: Optional[Callable[[str], Sequence[float]]] = None,
    text_key: str = "text",
    vector_key: str = "vector",
    k: int = 8,
    lambda_param: float = 0.7,
) -> list[dict[str, Any]]:
    """MMR-select a diverse context set, embedding candidates on the fly if needed.

    If candidates lack ``vector_key`` and an ``embed_fn`` is given, their text is
    embedded so MMR can run; otherwise non-vector candidates fall through to the
    tail. Convenience wrapper over ``mmr_select`` for the retrieval path.
    """
    if embed_fn is not None:
        for c in candidates:
            if not c.get(vector_key) and c.get(text_key):
                try:
                    c = c  # mutate in place is fine; caller owns the list
                    c[vector_key] = list(embed_fn(c[text_key]))
                except Exception:
                    pass
    return mmr_select(query_vec, candidates, vector_key=vector_key, k=k,
                      lambda_param=lambda_param)
