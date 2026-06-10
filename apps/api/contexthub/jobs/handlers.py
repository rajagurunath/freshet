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

summarize_batch
    Payload: {session_ids: list[str], provider: "openai-batch"|"local"|"default", model?}
    Batch-summarises multiple sessions using the chosen provider.
    - openai-batch: uploads JSONL to OpenAI Files API, creates a Batch, enqueues
      a batch_poll job scheduled 10 min from now.
    - local: loops sessions through a local openai-compatible server (e.g. Ollama).
    - default: falls back to the configured default provider.

batch_poll
    Payload: {batch_id, session_ids: list[str], model?, parent_job_id?}
    Polls an OpenAI batch for completion.  On success, writes summaries.
    When still in_progress, re-enqueues itself (scheduled +10 min).

summarize_pending
    Payload: {job_store?}
    Finds sessions with no summary and enqueues a summarize_batch job for them.
    Intended to run nightly (scheduled_for next 02:00 UTC).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_job_store() -> Any:
    """Return a JobStore pointed at the configured jobs DB.

    Handlers that need to enqueue follow-up jobs (e.g. batch_poll) use this
    instead of relying on payload injection, keeping the handler signature clean.
    """
    from contexthub.config import get_settings
    from contexthub.jobs.store import JobStore

    settings = get_settings()
    return JobStore(settings.jobs_db)


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
# summarize_batch handler
# ---------------------------------------------------------------------------

def summarize_batch_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch batch summarization to the appropriate provider.

    Payload keys:
      session_ids   list[str]  — session ids to summarize
      provider      str?       — "openai-batch" | "local" | "default"
      model         str?       — optional model override
      job_id        str?       — id of this job (injected by worker)
    """
    from contexthub.config import get_settings
    from contexthub.llm import get_llm
    from contexthub.llm_batch import (
        handle_summarize_batch_local,
        handle_summarize_batch_openai,
    )
    from contexthub.storage.vectors import get_vector_store

    settings = get_settings()
    session_ids: list[str] = payload.get("session_ids") or []
    provider: str = (payload.get("provider") or "default").lower()
    model: str | None = payload.get("model")
    job_id: str = payload.get("job_id") or "unknown"

    if not session_ids:
        logger.warning("summarize_batch_handler: no session_ids in payload")
        return {"session_count": 0, "provider": provider}

    # Fetch session rows from the vector store so we have transcript data.
    vectors = get_vector_store()

    def _fetch_sessions(ids: list[str]) -> list[dict[str, Any]]:
        """Resolve session rows and reconstruct lightweight session dicts."""
        results: list[dict[str, Any]] = []
        for sid in ids:
            row = vectors.get_session(sid)
            if not row:
                logger.warning("summarize_batch_handler: session %s not found, skipping", sid)
                continue
            # We need the blob to get the full transcript.
            from contexthub.storage.blob import get_blob_store
            blob = get_blob_store()
            author_id = row.get("author", "")
            raw = blob.get_session(author_id=author_id, session_id=sid)
            if raw:
                try:
                    envelope = json.loads(raw)
                    session_data = envelope.get("session") or envelope
                    session_data["id"] = sid
                    results.append(session_data)
                    continue
                except Exception:
                    pass
            # Fallback: minimal dict from catalog row
            results.append({
                "id": sid,
                "title": row.get("title", "Untitled"),
                "messages": [],
            })
        return results

    def _update_summary(session_id: str, summary: str) -> None:
        """Persist a summary to the sessions catalog row."""
        row = vectors.get_session(session_id)
        if row:
            updated = dict(row)
            updated["summary"] = summary
            vectors.upsert_session(updated)
            logger.info("summarize_batch_handler: updated summary for session %s", session_id)
        else:
            logger.warning("summarize_batch_handler: session %s not found for summary update", session_id)

    if provider == "openai-batch":
        try:
            from openai import OpenAI  # lazy import
        except ImportError as exc:
            logger.error("openai package not installed; cannot use openai-batch provider")
            raise

        sessions_data = _fetch_sessions(session_ids)
        openai_client = OpenAI(
            api_key=settings.openai_api_key or "not-needed",
            base_url=settings.openai_base_url or None,
        )
        # Get or create a job store for enqueueing the poll job.
        job_store = _get_job_store()
        return handle_summarize_batch_openai(
            session_ids=session_ids,
            sessions_data=sessions_data,
            openai_client=openai_client,
            job_store=job_store,
            job_id=job_id,
            model=model or settings.openai_model or "gpt-4o-mini",
        )

    if provider == "local":
        sessions_data = _fetch_sessions(session_ids)
        llm = get_llm(settings, provider_override="local", model_override=model)
        return handle_summarize_batch_local(
            sessions_data=sessions_data,
            llm_client=llm,
            update_summary_fn=_update_summary,
        )

    # "default": use the configured summarize_session handler for each session (sequential)
    session_count = 0
    errors = 0
    for sid in session_ids:
        row = vectors.get_session(sid)
        if not row:
            errors += 1
            continue
        from contexthub.storage.blob import get_blob_store
        blob = get_blob_store()
        raw = blob.get_session(author_id=row.get("author", ""), session_id=sid)
        if not raw:
            errors += 1
            continue
        try:
            envelope = json.loads(raw)
            session_json = json.dumps(envelope.get("session") or envelope)
            summarize_session_handler({"session_json": session_json, "provider": provider if provider != "default" else None})
            session_count += 1
        except Exception as exc:
            logger.warning("summarize_batch_handler: failed for session %s: %s", sid, exc)
            errors += 1

    return {"session_count": session_count, "errors": errors, "provider": provider}


# ---------------------------------------------------------------------------
# batch_poll handler
# ---------------------------------------------------------------------------

def batch_poll_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Poll an OpenAI batch for completion and write summaries.

    Payload keys:
      batch_id     str         — OpenAI batch id
      session_ids  list[str]   — session ids that were submitted
      model        str?        — model used (preserved for re-enqueue)
      job_id       str?        — id of this job (injected by worker; for re-enqueue tracking)
    """
    from contexthub.config import get_settings
    from contexthub.llm_batch import handle_batch_poll
    from contexthub.storage.vectors import get_vector_store

    settings = get_settings()
    batch_id: str = payload.get("batch_id") or ""
    session_ids: list[str] = payload.get("session_ids") or []
    model: str = payload.get("model") or settings.openai_model or "gpt-4o-mini"
    job_id: str = payload.get("job_id") or payload.get("parent_job_id") or "unknown"

    if not batch_id:
        raise ValueError("batch_poll_handler: missing batch_id in payload")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package not installed; cannot use batch_poll handler") from exc

    openai_client = OpenAI(
        api_key=settings.openai_api_key or "not-needed",
        base_url=settings.openai_base_url or None,
    )

    vectors = get_vector_store()

    def _update_summary(session_id: str, summary: str) -> None:
        row = vectors.get_session(session_id)
        if row:
            updated = dict(row)
            updated["summary"] = summary
            vectors.upsert_session(updated)
            logger.info("batch_poll_handler: updated summary for session %s", session_id)

    job_store = _get_job_store()

    return handle_batch_poll(
        batch_id=batch_id,
        session_ids=session_ids,
        openai_client=openai_client,
        update_summary_fn=_update_summary,
        job_store=job_store,
        job_id=job_id,
        model=model,
    )


# ---------------------------------------------------------------------------
# summarize_pending handler
# ---------------------------------------------------------------------------

def summarize_pending_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Find sessions with no summary and enqueue a summarize_batch job.

    Payload keys:
      job_store    JobStore?   — injected by the worker; needed to enqueue the batch job.
      provider     str?        — provider to use for the batch (default: "default")
      model        str?        — optional model override

    This handler is intended to run nightly (scheduled_for next 02:00 UTC).
    It is also invoked on startup when no pending summarize_pending job exists.
    """
    from contexthub.storage.vectors import get_vector_store

    vectors = get_vector_store()
    provider: str = payload.get("provider") or "default"
    model: str | None = payload.get("model")
    # Accept a directly-injected job_store (useful in tests); otherwise get from settings.
    job_store = payload.get("job_store") or _get_job_store()

    # List all sessions (up to 500) and find those without a summary.
    result = vectors.list_sessions(limit=500, offset=0, sort="created_at", order="desc")
    pending_ids: list[str] = [
        row["id"]
        for row in result.get("items", [])
        if not (row.get("summary") or "").strip()
    ]

    pending_count = len(pending_ids)
    logger.info("summarize_pending_handler: %d sessions need summaries", pending_count)

    if not pending_ids:
        return {"pending_count": 0}

    if job_store is not None:
        enqueued_job_id = job_store.enqueue(
            kind="summarize_batch",
            payload={
                "session_ids": pending_ids,
                "provider": provider,
                "model": model,
            },
        )
        logger.info(
            "summarize_pending_handler: enqueued summarize_batch job %s for %d sessions",
            enqueued_job_id,
            pending_count,
        )
        return {"pending_count": pending_count, "batch_job_id": enqueued_job_id}

    return {"pending_count": pending_count}


# ---------------------------------------------------------------------------
# graph_extract handler (Task 13)
# ---------------------------------------------------------------------------

def graph_extract_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract a knowledge graph from a session and persist it.

    Payload keys:
      session_id  str  — id of the session to extract from
      provider    str? — optional LLM provider override
      model       str? — optional model override

    Reads the session catalog row for its summary + visibility, reconstructs the
    session from the raw blob (for transcript fallback), runs LLM extraction, and
    marks the session row graph_extracted=True so the harvester does not re-queue it.
    """
    from contexthub.graph.extract import extract_graph
    from contexthub.graph.store import get_graph_store
    from contexthub.models import NormalizedSession
    from contexthub.storage.blob import get_blob_store
    from contexthub.storage.vectors import get_vector_store

    session_id = payload.get("session_id") or ""
    if not session_id:
        return {"session_id": "", "nodes_upserted": 0, "edges_upserted": 0, "error": "missing session_id"}

    vectors = get_vector_store()
    row = vectors.get_session(session_id)
    if not row:
        logger.warning("graph_extract_handler: session %s not found", session_id)
        return {"session_id": session_id, "nodes_upserted": 0, "edges_upserted": 0, "error": "not_found"}

    summary = row.get("summary") or ""
    visibility = row.get("visibility") or "company"
    author = row.get("author") or None
    team = row.get("team") or None

    # Reconstruct the session from the raw blob for transcript fallback.
    session: NormalizedSession
    blob = get_blob_store()
    raw = blob.get_session(author_id=author or "", session_id=session_id)
    if raw:
        try:
            envelope = json.loads(raw)
            session_data = envelope.get("session") or envelope
            session_data["id"] = session_id
            session = NormalizedSession.model_validate(session_data)
        except Exception:
            session = NormalizedSession(id=session_id, tool=row.get("tool", "claude-code"), title=row.get("title", ""))
    else:
        session = NormalizedSession(id=session_id, tool=row.get("tool", "claude-code"), title=row.get("title", ""))

    store = get_graph_store()
    result = extract_graph(
        session=session,
        summary=summary,
        store=store,
        provider=payload.get("provider"),
        model=payload.get("model"),
        visibility=visibility,
        author=author,
        team=team,
    )

    # Mark the session so the harvester does not re-enqueue it.
    try:
        vectors.mark_graph_extracted(session_id)
    except Exception:
        logger.exception("graph_extract_handler: failed to mark session %s extracted", session_id)

    return result


# ---------------------------------------------------------------------------
# harvest_check handler (Task 12)
# ---------------------------------------------------------------------------

def harvest_check_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Delegate to contexthub.jobs.harvest.harvest_check_handler.

    Keeping the registry thin: actual implementation lives in harvest.py.
    """
    from contexthub.jobs.harvest import harvest_check_handler as _impl
    return _impl(payload)


# ---------------------------------------------------------------------------
# rules_extract handler (Task 14)
# ---------------------------------------------------------------------------

def rules_extract_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract recurring rules/preferences from an author's recent session summaries.

    Payload keys:
      author      str?  — user id whose sessions are mined (filters list_sessions by author)
      n_sessions  int?  — how many recent sessions to use (default 20)
      provider    str?  — optional LLM provider override
      model       str?  — optional model override

    Reads the last N session catalog rows for the author, concatenates their
    summaries (falling back to preview), calls the LLM extraction prompt, and
    persists any new rules in 'proposed' status.  Near-duplicates of existing
    rules are silently skipped.
    """
    from contexthub.rules.extract import extract_rules
    from contexthub.rules.store import get_rules_store
    from contexthub.storage.vectors import get_vector_store

    author: str | None = payload.get("author")
    n_sessions: int = int(payload.get("n_sessions") or 20)
    provider: str | None = payload.get("provider")
    model: str | None = payload.get("model")

    vectors = get_vector_store()

    # Fetch recent sessions by the author (or all sessions if no author filter).
    filters: dict[str, Any] = {}
    if author:
        filters["author"] = author

    result = vectors.list_sessions(
        filters=filters or None,
        limit=n_sessions,
        offset=0,
        sort="created_at",
        order="desc",
    )

    rows = result.get("items", [])
    if not rows:
        logger.info("rules_extract_handler: no sessions found for author=%s", author)
        return {"rules_upserted": 0, "rules_skipped_duplicate": 0, "session_count": 0}

    # Build the session excerpts: prefer summary, fall back to preview.
    parts: list[str] = []
    for row in rows:
        sid = row.get("id", "")
        summary = (row.get("summary") or "").strip()
        preview = (row.get("preview") or "").strip()
        excerpt = summary or preview
        if excerpt:
            parts.append(f"[session {sid}]\n{excerpt}")

    session_excerpts = "\n\n".join(parts)
    if not session_excerpts.strip():
        logger.info("rules_extract_handler: all sessions lack summary/preview — nothing to mine")
        return {"rules_upserted": 0, "rules_skipped_duplicate": 0, "session_count": len(rows)}

    store = get_rules_store()
    extraction_result = extract_rules(
        session_excerpts=session_excerpts,
        store=store,
        author=author,
        provider=provider,
        model=model,
    )

    logger.info(
        "rules_extract_handler: author=%s, %d sessions → %d rules upserted, %d skipped",
        author,
        len(rows),
        extraction_result.get("rules_upserted", 0),
        extraction_result.get("rules_skipped_duplicate", 0),
    )

    return {
        "session_count": len(rows),
        "rules_upserted": extraction_result.get("rules_upserted", 0),
        "rules_skipped_duplicate": extraction_result.get("rules_skipped_duplicate", 0),
    }


# ---------------------------------------------------------------------------
# Registry: maps kind → handler callable
# ---------------------------------------------------------------------------

HANDLER_REGISTRY: dict[str, Any] = {
    "summarize_session": summarize_session_handler,
    "summarize_batch": summarize_batch_handler,
    "batch_poll": batch_poll_handler,
    "summarize_pending": summarize_pending_handler,
    "graph_extract": graph_extract_handler,
    "harvest_check": harvest_check_handler,
    "rules_extract": rules_extract_handler,
}
