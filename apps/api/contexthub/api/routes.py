"""FastAPI route definitions for the Context Hub API.

Endpoints:
  POST   /v1/sessions         — ingest a session envelope
  GET    /v1/sessions         — list catalog (with optional filters)
  GET    /v1/sessions/{id}    — fetch catalog row + raw blob
  POST   /v1/summarize        — summarize a session
  POST   /v1/query            — RAG query
  GET    /v1/stats            — aggregate statistics
  GET    /healthz             — liveness probe (no auth)

All endpoints except /healthz require a valid Bearer token.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from contexthub.config import Settings, get_settings
from contexthub.deps import require_api_key
from contexthub.embeddings import get_embedder
from contexthub.ingest.chunker import build_chunks
from contexthub.ingest.redact import redact_text
from contexthub.models import (
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    SessionCatalogRow,
    StatsResponse,
    SummarizeRequest,
    SummarizeResponse,
)
from contexthub.rag.agent import answer_query
from contexthub.rag.summarize import summarize_session
from contexthub.storage.blob import get_blob_store
from contexthub.storage.vectors import get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Healthz — no auth
# ---------------------------------------------------------------------------

@router.get("/healthz", tags=["meta"])
def healthz():
    """Liveness probe — always returns 200 OK."""
    return {"status": "ok"}


@router.get("/v1/providers", tags=["meta"])
def providers(
    _token: str = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """Report which LLM providers are usable on this host + the current default."""
    from contexthub.llm import available_providers

    return {
        "default": (settings.llm_provider or "claude-cli").lower(),
        "model": settings.llm_model,
        "providers": available_providers(settings),
    }


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@router.post("/v1/sessions", response_model=IngestResponse, tags=["sessions"])
def ingest_session(
    body: IngestRequest,
    _token: str = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """Ingest a session envelope.

    Pipeline:
      1. Optionally re-redact text fields if body.redacted is False.
      2. Store raw JSON in blob store (S3 or local).
      3. Build text chunks (summary + sliding window over messages).
      4. Embed chunks.
      5. Upsert chunks + catalog row into LanceDB.
    """
    session = body.session

    # Optional server-side redaction (belt-and-suspenders)
    if not body.redacted:
        for msg in session.messages:
            msg.text, _ = redact_text(msg.text)
            if msg.thinking:
                msg.thinking, _ = redact_text(msg.thinking)

    # 1. Persist raw JSON
    blob = get_blob_store()
    raw_json = body.model_dump_json()
    blob_uri = blob.put_session(
        author_id=body.author.id,
        session_id=session.id,
        raw_json=raw_json,
    )

    # 2. Determine summary
    effective_summary: Optional[str] = body.summary
    if not effective_summary:
        effective_summary = summarize_session(session, settings)

    # 3. Build chunks
    chunks = build_chunks(session, summary=effective_summary)

    # 4. Embed
    embedder = get_embedder()
    texts = [c.text for c in chunks]
    vectors_list = embedder.embed_texts(texts) if texts else []

    # 5. Upsert into LanceDB
    vectors = get_vector_store(embedding_dim=embedder.dim)
    created_at = datetime.now(timezone.utc).isoformat()

    chunk_rows = [
        {
            "id": chunk.id,
            "session_id": session.id,
            "tool": session.tool,
            "category": body.category,
            "author": body.author.id,
            "project": session.project or "",
            "visibility": body.visibility,
            "text": chunk.text,
            "vector": vec,
            "created_at": created_at,
        }
        for chunk, vec in zip(chunks, vectors_list)
    ]
    vectors.upsert_chunks(chunk_rows)

    catalog_row = {
        "id": session.id,
        "tool": session.tool,
        "title": session.title,
        "category": body.category,
        "author": body.author.id,
        "project": session.project or "",
        "visibility": body.visibility,
        "message_count": session.message_count,
        "models": json.dumps(session.models),
        "preview": session.preview,
        "created_at": created_at,
        "blob_uri": blob_uri,
        "summary": effective_summary or "",
    }
    vectors.upsert_session(catalog_row)

    logger.info("Ingested session %s — %d chunks", session.id, len(chunks))
    return IngestResponse(
        session_id=session.id,
        blob_uri=blob_uri,
        chunks_indexed=len(chunks),
        summary_used=bool(effective_summary),
    )


# ---------------------------------------------------------------------------
# Session catalog
# ---------------------------------------------------------------------------

@router.get("/v1/sessions", response_model=list[SessionCatalogRow], tags=["sessions"])
def list_sessions(
    category: Optional[str] = Query(None),
    tool: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    visibility: Optional[str] = Query(None),
    _token: str = Depends(require_api_key),
):
    """Return catalog rows, optionally filtered by metadata fields."""
    filters: dict = {}
    if category:
        filters["category"] = category
    if tool:
        filters["tool"] = tool
    if project:
        filters["project"] = project
    if author:
        filters["author"] = author
    if visibility:
        filters["visibility"] = visibility

    vectors = get_vector_store()
    rows = vectors.list_sessions(filters or None)
    result = []
    for r in rows:
        result.append(SessionCatalogRow(
            id=r["id"],
            tool=r["tool"],
            title=r["title"],
            category=r["category"],
            author=r.get("author"),
            project=r.get("project"),
            visibility=r["visibility"],
            message_count=int(r.get("message_count", 0)),
            models=json.loads(r.get("models") or "[]"),
            preview=r.get("preview", ""),
            created_at=r.get("created_at", ""),
            blob_uri=r.get("blob_uri", ""),
            summary=r.get("summary") or None,
        ))
    return result


@router.get("/v1/sessions/{session_id}", tags=["sessions"])
def get_session(
    session_id: str,
    _token: str = Depends(require_api_key),
):
    """Return catalog row plus the raw blob JSON for a specific session."""
    vectors = get_vector_store()
    row = vectors.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    # Fetch raw blob
    blob = get_blob_store()
    author_id = row.get("author", "")
    raw = blob.get_session(author_id=author_id, session_id=session_id)

    return {
        "catalog": row,
        "raw": json.loads(raw) if raw else None,
    }


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

@router.post("/v1/summarize", response_model=SummarizeResponse, tags=["rag"])
def summarize(
    body: SummarizeRequest,
    _token: str = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """Generate a structured summary for a session (does not ingest it)."""
    summary = summarize_session(body.session, settings, provider=body.provider, model=body.model)
    return SummarizeResponse(summary=summary)


# ---------------------------------------------------------------------------
# RAG query
# ---------------------------------------------------------------------------

@router.post("/v1/query", response_model=QueryResponse, tags=["rag"])
def query(
    body: QueryRequest,
    _token: str = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """RAG query across all ingested sessions."""
    embedder = get_embedder()
    vectors = get_vector_store(embedding_dim=embedder.dim)
    return answer_query(req=body, vectors=vectors, embedder=embedder, settings=settings)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/v1/stats", response_model=StatsResponse, tags=["meta"])
def stats(
    _token: str = Depends(require_api_key),
):
    """Aggregate statistics: total sessions, chunks, breakdown by tool/category."""
    vectors = get_vector_store()
    data = vectors.stats()
    return StatsResponse(
        total_sessions=data["total_sessions"],
        total_chunks=data["total_chunks"],
        sessions_by_tool=data["sessions_by_tool"],
        sessions_by_category=data["sessions_by_category"],
    )
