"""Tests for the cross-session retrieval evaluation harness.

Two layers:
  1. Pure metric math (deterministic, no embeddings) — guards the numbers we'll
     report as the product's moat proof.
  2. Corpus structure + end-to-end plumbing with the offline 'hash' embedder —
     confirms ingest→retrieve→score runs without network/LLM. (The 'hash'
     embedder is not semantic, so we don't assert on retrieval *quality* here;
     that's what the local-MiniLM benchmark run is for.)
"""

from __future__ import annotations

import math

from contexthub.eval.metrics import (
    average_precision,
    evaluate,
    hit_at_k,
    mrr,
    ndcg_at_k,
    recall_at_k,
)


# ---------------------------------------------------------------------------
# 1. Metric math
# ---------------------------------------------------------------------------

def test_recall_and_hit_basic():
    ranking = ["a", "b", "c", "d"]
    assert recall_at_k(ranking, ["b", "d"], 4) == 1.0
    assert recall_at_k(ranking, ["b", "d"], 2) == 0.5   # only b in top-2
    assert recall_at_k(ranking, ["z"], 4) == 0.0
    assert hit_at_k(ranking, ["d"], 4) == 1.0
    assert hit_at_k(ranking, ["d"], 2) == 0.0
    assert hit_at_k(ranking, ["a"], 1) == 1.0


def test_mrr():
    assert mrr(["a", "b", "c"], ["b"]) == 0.5         # first gold at rank 2
    assert mrr(["a", "b", "c"], ["a"]) == 1.0
    assert mrr(["a", "b", "c"], ["z"]) == 0.0
    # First gold encountered wins even if multiple gold exist.
    assert mrr(["a", "b", "c"], ["c", "b"]) == 0.5


def test_ndcg_perfect_and_imperfect():
    # All gold at the top → perfect nDCG.
    assert ndcg_at_k(["a", "b", "c"], ["a", "b"], 3) == 1.0
    # Single gold at rank 2: DCG = 1/log2(3); IDCG = 1/log2(2) = 1.
    got = ndcg_at_k(["x", "a", "y"], ["a"], 3)
    assert math.isclose(got, 1.0 / math.log2(3), rel_tol=1e-9)
    assert ndcg_at_k(["x", "y"], ["z"], 2) == 0.0


def test_average_precision():
    # Gold at ranks 1 and 3: AP = (1/1 + 2/3) / 2.
    ap = average_precision(["a", "x", "b", "y"], ["a", "b"], 4)
    assert math.isclose(ap, (1.0 + 2.0 / 3.0) / 2.0, rel_tol=1e-9)
    assert average_precision(["a", "b"], ["z"], 2) == 0.0


def test_evaluate_aggregates_means():
    # Two questions; a perfect retriever scores 1.0 across the board.
    questions = [
        {"question": "q1", "gold": ["a"], "type": "lookup"},
        {"question": "q2", "gold": ["b"], "type": "lookup"},
    ]

    def perfect(q: str, k: int):
        return ["a"] if q == "q1" else ["b"]

    m = evaluate(perfect, questions, ks=(1, 3), retrieve_k=5)
    assert m["hit@1"] == 1.0
    assert m["recall@1"] == 1.0
    assert m["mrr"] == 1.0
    assert m["ndcg@1"] == 1.0


def test_evaluate_partial_retriever():
    questions = [
        {"question": "q1", "gold": ["a"], "type": "lookup"},
        {"question": "q2", "gold": ["b"], "type": "lookup"},
    ]

    def half(q: str, k: int):
        return ["a"] if q == "q1" else ["wrong"]

    m = evaluate(half, questions, ks=(1,), retrieve_k=5)
    assert m["hit@1"] == 0.5


# ---------------------------------------------------------------------------
# 2. Corpus structure
# ---------------------------------------------------------------------------

def test_corpus_is_deterministic_and_well_formed():
    from contexthub.eval.corpus import build_corpus

    c1 = build_corpus()
    c2 = build_corpus()
    ids1 = [it.session.id for it in c1.items]
    ids2 = [it.session.id for it in c2.items]
    assert ids1 == ids2, "corpus must be deterministic"
    assert len(ids1) == len(set(ids1)), "session ids must be unique"

    # Every question's gold ids must exist in the corpus.
    known = set(ids1)
    for q in c1.questions:
        assert q["gold"], q
        for g in q["gold"]:
            assert g in known, f"gold {g} not in corpus"
        assert q["type"] in {"lookup", "synthesis", "alias", "bridge"}

    # There must be cross-session entity sharing (the graph's reason to exist):
    # at least one entity appears in two different sessions.
    from collections import Counter
    ent_sessions: dict[str, set[str]] = {}
    for it in c1.items:
        for e in it.entities:
            ent_sessions.setdefault(e, set()).add(it.session.id)
    shared = {e: s for e, s in ent_sessions.items() if len(s) >= 2}
    assert shared, "expected at least one entity shared across sessions"

    # And the answer must be buried: transcripts should be reasonably long.
    assert all(len(it.session.messages) >= 3 for it in c1.items)


def test_corpus_has_synthesis_and_alias_questions():
    from contexthub.eval.corpus import build_corpus

    c = build_corpus()
    types = {q["type"] for q in c.questions}
    assert "synthesis" in types, "need multi-session synthesis questions"
    assert "alias" in types, "need alias (vocabulary-mismatch) questions"


# ---------------------------------------------------------------------------
# 3. End-to-end plumbing (offline hash embedder)
# ---------------------------------------------------------------------------

def test_harness_ingests_and_scores_offline():
    from contexthub.eval.harness import build_env

    env = build_env(embedder_provider="hash", body_chars=800, with_graph=True)
    try:
        # Graph populated from planted entities.
        nodes = env.graph.list_nodes()
        names = {n["name"] for n in nodes}
        assert "checkout" in names and "session-service" in names

        # Baseline retriever returns distinct session ids and scores without error.
        retr = env.baseline_retriever()
        ranked = retr("checkout flow payments", 10)
        assert ranked == list(dict.fromkeys(ranked)), "ids must be de-duplicated"
        assert all(isinstance(s, str) and s for s in ranked)

        m = evaluate(retr, env.corpus.questions, ks=(1, 3, 10))
        # Metrics are well-formed probabilities in [0, 1].
        for key, val in m.items():
            assert 0.0 <= val <= 1.0, (key, val)
    finally:
        env.close()
