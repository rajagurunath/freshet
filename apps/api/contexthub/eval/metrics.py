"""Native IR metrics for cross-session retrieval — no external deps.

A "ranking" is an ordered list of session ids (best first). "gold" is the set of
session ids that genuinely answer the question. We support multi-gold questions
(the cross-session-synthesis case is the whole point of the product), so:

- Recall@k  — fraction of gold ids found in the top k.
- Hit@k     — 1.0 if *any* gold id is in the top k, else 0.0 (a.k.a. success@k).
- MRR       — 1 / rank of the first gold id (0 if none).
- nDCG@k    — graded gain with binary relevance, ideal-DCG-normalised; rewards
              ranking gold ids higher and handles multi-gold correctly.
- AP        — average precision (single-query mAP component).

These match the ``ranx`` definitions (verified against its formulas) so numbers
are comparable if we ever swap in ranx for speed at scale. Implemented in pure
Python/stdlib to keep the eval path offline and dependency-free.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from statistics import mean
from typing import Callable


def _topk(ranking: Sequence[str], k: int) -> list[str]:
    return list(ranking[: max(0, k)])


def recall_at_k(ranking: Sequence[str], gold: Iterable[str], k: int) -> float:
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    found = sum(1 for sid in set(_topk(ranking, k)) if sid in gold_set)
    return found / len(gold_set)


def hit_at_k(ranking: Sequence[str], gold: Iterable[str], k: int) -> float:
    gold_set = set(gold)
    return 1.0 if any(sid in gold_set for sid in _topk(ranking, k)) else 0.0


def mrr(ranking: Sequence[str], gold: Iterable[str]) -> float:
    gold_set = set(gold)
    for i, sid in enumerate(ranking):
        if sid in gold_set:
            return 1.0 / (i + 1)
    return 0.0


def ndcg_at_k(ranking: Sequence[str], gold: Iterable[str], k: int) -> float:
    """Binary-relevance nDCG@k.

    DCG = sum over the top-k of rel_i / log2(i + 2) (0-indexed i).
    IDCG = the DCG of the best possible ordering (all gold first), capped at k.
    """
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    dcg = 0.0
    for i, sid in enumerate(_topk(ranking, k)):
        if sid in gold_set:
            dcg += 1.0 / math.log2(i + 2)
    ideal_hits = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def average_precision(ranking: Sequence[str], gold: Iterable[str], k: int | None = None) -> float:
    """Average precision (the per-query term of mAP).

    Precision is averaged at the ranks where a gold id is retrieved, normalised
    by the number of gold ids (so a query can reach 1.0 only by ranking every
    gold id at the very top).
    """
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    ranked = _topk(ranking, k) if k is not None else list(ranking)
    hits = 0
    score = 0.0
    seen: set[str] = set()
    for i, sid in enumerate(ranked):
        if sid in gold_set and sid not in seen:
            seen.add(sid)
            hits += 1
            score += hits / (i + 1)
    return score / len(gold_set)


# ---------------------------------------------------------------------------
# Aggregation over a question set
# ---------------------------------------------------------------------------

# A retriever maps (question, k) -> ranked list of session ids (best first).
Retriever = Callable[[str, int], Sequence[str]]


def evaluate(
    retriever: Retriever,
    questions: Sequence[dict],
    ks: Sequence[int] = (1, 3, 5, 10),
    retrieve_k: int = 20,
) -> dict[str, float]:
    """Run ``retriever`` over a silver question set and return mean metrics.

    Each question dict needs at least ``{"question": str, "gold": [session_id...]}``.
    ``retrieve_k`` is how deep we ask the retriever to return (>= max(ks) so the
    @k metrics are well-defined). Returns a flat dict of mean metric values keyed
    like ``recall@10``, ``hit@3``, ``mrr``, ``ndcg@10``, ``map``.
    """
    per_q: dict[str, list[float]] = {}

    def add(key: str, val: float) -> None:
        per_q.setdefault(key, []).append(val)

    max_k = max(ks)
    for q in questions:
        gold = q["gold"]
        ranking = list(retriever(q["question"], max(retrieve_k, max_k)))
        for k in ks:
            add(f"recall@{k}", recall_at_k(ranking, gold, k))
            add(f"hit@{k}", hit_at_k(ranking, gold, k))
            add(f"ndcg@{k}", ndcg_at_k(ranking, gold, k))
        add("mrr", mrr(ranking, gold))
        add("map", average_precision(ranking, gold, max_k))

    return {key: mean(vals) if vals else 0.0 for key, vals in per_q.items()}


def evaluate_by_type(
    retriever: Retriever,
    questions: Sequence[dict],
    ks: Sequence[int] = (1, 3, 5, 10),
    retrieve_k: int = 20,
) -> dict[str, dict[str, float]]:
    """Like ``evaluate`` but bucketed by each question's ``type`` field.

    Returns ``{type: metrics}`` plus an ``"all"`` bucket. Lets us see, e.g., that
    the graph arm helps the ``synthesis`` bucket most — which is the moat claim.
    """
    buckets: dict[str, list[dict]] = {"all": list(questions)}
    for q in questions:
        buckets.setdefault(q.get("type", "unknown"), []).append(q)
    return {
        name: evaluate(retriever, qs, ks=ks, retrieve_k=retrieve_k)
        for name, qs in buckets.items()
    }
