"""Cross-session retrieval evaluation harness.

The strategic point of this package: the one defensible moat for this product is
GraphRAG *over the conversation corpus* — and skeptics reasonably believe good
chunked vector search already wins. So the claim must be **proven, not asserted**.

This harness builds a deterministic synthetic multi-session corpus with planted,
cross-session entities, generates silver questions whose gold answer is the
source session id(s), and scores any ``retrieve(question, k) -> [session_id]``
callable with standard IR metrics (Recall@k, Hit@k, MRR, nDCG@k). It lets us
A/B every retrieval slice (graph arm, reranker, MMR, …) against the vanilla
vector+FTS baseline and report the lift with numbers.

Everything here is fully offline and dependency-free (metrics are implemented
natively — no ranx/pytrec_eval). The real benchmark run uses the local MiniLM
embedder; tests use the deterministic ``hash`` embedder to exercise the plumbing.
"""

from contexthub.eval.metrics import (
    average_precision,
    evaluate,
    hit_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

__all__ = [
    "average_precision",
    "evaluate",
    "hit_at_k",
    "mrr",
    "ndcg_at_k",
    "recall_at_k",
]
