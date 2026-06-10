"""Job handler registry.

Each handler is a synchronous callable:
    def handler(payload: dict) -> dict

Handlers run in asyncio.to_thread so they may perform blocking I/O.
They must NOT access the asyncio event loop directly.

Registered handlers
-------------------
summarize_session
    Payload: {session_id, session_json (str), provider?, model?}
    Summarises the session using the configured LLM, then updates the
    session catalog row's 'summary' field and re-embeds the first chunk.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def summarize_session_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Summarize a session and update its stored catalog row.

    Payload keys:
      session_json  str  — JSON-serialised NormalizedSession
      provider      str? — optional LLM provider override
      model         str? — optional model override
    """
    from contexthub.config import get_settings
    from contexthub.embeddings import get_embedder
    from contexthub.models import NormalizedSession
    from contexthub.rag.summarize import summarize_session
    from contexthub.storage.vectors import get_vector_store

    settings = get_settings()

    raw = payload.get("session_json") or "{}"
    session_data = json.loads(raw)
    session = NormalizedSession.model_validate(session_data)
    provider = payload.get("provider")
    model = payload.get("model")

    logger.info("summarize_session_handler: summarising session %s", session.id)
    summary = summarize_session(session, settings, provider=provider, model=model)

    # Update the catalog row with the new summary
    vectors = get_vector_store()
    existing = vectors.get_session(session.id)
    if existing:
        updated_row = dict(existing)
        updated_row["summary"] = summary or ""
        vectors.upsert_session(updated_row)
        logger.info("summarize_session_handler: updated summary for session %s", session.id)

        # Re-embed the first chunk (the summary chunk) so it reflects the new summary.
        embedder = get_embedder()
        if summary:
            from contexthub.ingest.chunker import build_chunks
            chunks = build_chunks(session, summary=summary)
            if chunks:
                first_chunk = chunks[0]
                vecs = embedder.embed_texts([first_chunk.text])
                if vecs:
                    chunk_row = {
                        "id": first_chunk.id,
                        "session_id": session.id,
                        "tool": session.tool,
                        "category": existing.get("category", "engineering"),
                        "author": existing.get("author", ""),
                        "team": existing.get("team", ""),
                        "project": session.project or "",
                        "visibility": existing.get("visibility", "company"),
                        "text": first_chunk.text,
                        "vector": vecs[0],
                        "created_at": existing.get("created_at", ""),
                    }
                    vectors.upsert_chunks([chunk_row])
                    logger.debug(
                        "summarize_session_handler: re-embedded summary chunk for session %s",
                        session.id,
                    )

    return {"session_id": session.id, "summary_length": len(summary) if summary else 0}


# ---------------------------------------------------------------------------
# Registry: maps kind → handler callable
# ---------------------------------------------------------------------------

HANDLER_REGISTRY: dict[str, Any] = {
    "summarize_session": summarize_session_handler,
}
