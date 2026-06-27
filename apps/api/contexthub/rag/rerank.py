"""Cross-encoder reranking — the highest-leverage retrieval-quality upgrade.

The bi-encoder (MiniLM) embeds the query and each chunk *independently*, which is
fast but lossy. A cross-encoder scores the (query, chunk) pair *jointly*, seeing
token-level interaction, so it reorders a candidate set much more accurately. The
standard pattern: retrieve a wide candidate set with vector+FTS(+graph), then
rerank the top of it and keep the best few.

We use **FlashRank** — an ONNX, deliberately torch-free reranker that ships tiny
CPU models (``ms-marco-TinyBERT-L-2-v2`` ~4MB). It's an optional extra
(``contexthub[rerank]``); when it isn't installed, ``rerank`` is an identity
pass-through, so the retrieval path always works offline without it.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Tiny, CPU-friendly default; callers can override via rerank(model=...).
_DEFAULT_MODEL = "ms-marco-TinyBERT-L-2-v2"


def rerank_available() -> bool:
    try:
        import flashrank  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=2)
def _get_ranker(model_name: str):
    """Build (and cache) a FlashRank Ranker. Returns None if unavailable."""
    try:
        from flashrank import Ranker
    except Exception:
        return None
    try:
        return Ranker(model_name=model_name)
    except Exception:
        logger.warning("rerank: failed to load FlashRank model %s", model_name, exc_info=True)
        return None


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    top_k: Optional[int] = None,
    text_key: str = "text",
    model: str = _DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    """Reorder ``candidates`` by cross-encoder relevance to ``query``.

    Each candidate is a dict carrying at least ``text_key``. Returns the same
    dicts reordered (and truncated to ``top_k`` if given), each annotated with a
    ``_rerank_score``. If FlashRank is not installed or the candidate set is
    trivial, returns the input order unchanged (annotation skipped) — a safe,
    offline no-op so callers never depend on the optional extra.
    """
    if not candidates or len(candidates) == 1:
        return candidates[:top_k] if top_k else candidates

    ranker = _get_ranker(model)
    if ranker is None:
        return candidates[:top_k] if top_k else candidates

    try:
        from flashrank import RerankRequest

        passages = [
            {"id": i, "text": (c.get(text_key) or ""), "meta": {}}
            for i, c in enumerate(candidates)
        ]
        ranked = ranker.rerank(RerankRequest(query=query, passages=passages))
        out: list[dict[str, Any]] = []
        for r in ranked:
            idx = int(r["id"])
            item = dict(candidates[idx])
            item["_rerank_score"] = float(r.get("score", 0.0))
            out.append(item)
        return out[:top_k] if top_k else out
    except Exception:
        logger.warning("rerank: FlashRank scoring failed; returning original order", exc_info=True)
        return candidates[:top_k] if top_k else candidates
