"""Tests for cross-encoder reranking (Slice S4).

The identity-fallback behaviour (no FlashRank installed, or trivial input) is
always tested. The actual reranking quality test is skipped when the optional
``[rerank]`` extra is absent, keeping CI green offline.
"""

from __future__ import annotations

import pytest

from contexthub.rag.rerank import rerank, rerank_available


def test_rerank_trivial_inputs_are_passthrough():
    assert rerank("q", []) == []
    one = [{"session_id": "a", "text": "hello"}]
    assert rerank("q", one) == one


def test_rerank_respects_top_k_passthrough_when_unavailable(monkeypatch):
    # Force the unavailable path: _get_ranker returns None → identity + truncate.
    import contexthub.rag.rerank as rr
    monkeypatch.setattr(rr, "_get_ranker", lambda model: None)
    cands = [{"session_id": str(i), "text": f"t{i}"} for i in range(5)]
    out = rerank("q", cands, top_k=3)
    assert [c["session_id"] for c in out] == ["0", "1", "2"]  # order preserved


@pytest.mark.skipif(not rerank_available(), reason="FlashRank optional extra not installed")
def test_rerank_orders_relevant_first():
    cands = [
        {"session_id": "irrelevant", "text": "the weather is sunny and warm today"},
        {"session_id": "relevant", "text": "we implemented the checkout flow in the payments service"},
    ]
    out = rerank("how is checkout implemented in payments", cands, top_k=2)
    assert out[0]["session_id"] == "relevant"
    assert "_rerank_score" in out[0]
