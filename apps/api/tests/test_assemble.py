"""Tests for MMR diverse context assembly (Slice S7)."""

from __future__ import annotations

from contexthub.rag.assemble import mmr_select


def test_mmr_prefers_diversity_over_near_duplicates():
    # Query aligns with the x-axis. a and a' are near-identical; b is a distinct
    # but still-relevant direction. MMR should pick a then b (not a then a').
    q = [1.0, 0.0]
    cands = [
        {"id": "a",  "vector": [1.0, 0.0]},
        {"id": "a2", "vector": [0.99, 0.01]},
        {"id": "b",  "vector": [0.7, 0.7]},
    ]
    # Diversity-leaning lambda: the near-duplicate a2 is penalised for redundancy
    # so the distinct-but-relevant b is chosen second.
    picked = [c["id"] for c in mmr_select(q, cands, k=2, lambda_param=0.3)]
    assert picked[0] == "a"
    assert picked[1] == "b", f"MMR should diversify, got {picked}"


def test_mmr_pure_relevance_when_lambda_one():
    q = [1.0, 0.0]
    cands = [
        {"id": "far", "vector": [0.0, 1.0]},
        {"id": "near", "vector": [0.95, 0.05]},
    ]
    picked = [c["id"] for c in mmr_select(q, cands, k=2, lambda_param=1.0)]
    assert picked[0] == "near"  # most relevant first


def test_mmr_handles_missing_vectors_without_dropping():
    q = [1.0, 0.0]
    cands = [
        {"id": "v", "vector": [1.0, 0.0]},
        {"id": "novec"},  # no vector → appended to tail, not dropped
    ]
    picked = [c["id"] for c in mmr_select(q, cands, k=5)]
    assert set(picked) == {"v", "novec"}
    assert picked[0] == "v"


def test_mmr_respects_k():
    q = [1.0, 0.0]
    cands = [{"id": str(i), "vector": [1.0, i * 0.1]} for i in range(6)]
    assert len(mmr_select(q, cands, k=3)) == 3
