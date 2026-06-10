"""FastAPI route definitions for the Context Hub API.

Endpoints:
  POST   /v1/sessions         — ingest a session envelope
  GET    /v1/sessions         — list catalog (paginated, sorted, filtered)
  GET    /v1/sessions/{id}    — fetch SessionDetail (catalog + raw blob)
  POST   /v1/summarize        — summarize a session
  POST   /v1/query            — RAG query
  GET    /v1/stats            — aggregate statistics
  GET    /healthz             — liveness probe (no auth)

All endpoints except /healthz require a valid Bearer token.
"""

from __future__ import annotations

import hashlib
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
    SessionDetail,
    SessionPage,
    StatsResponse,
    SummarizeRequest,
    SummarizeResponse,
)
from contexthub.rag.agent import answer_query
from contexthub.rag.summarize import summarize_session
from contexthub.storage.blob import get_blob_store
from contexthub.storage.vectors import SORT_FIELDS, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


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
# Helpers
# ---------------------------------------------------------------------------

def _compute_content_hash(body: IngestRequest, summary: Optional[str]) -> str:
    """Compute a deterministic SHA-256 hash of the canonical session content.

    The hash covers the session JSON (sorted keys) plus the effective summary,
    so that a re-ingest with identical content is idempotent.
    """
    session_dict = body.session.model_dump(mode="json")
    canonical = json.dumps(session_dict, sort_keys=True, ensure_ascii=False)
    combined = canonical + "\n---\n" + (summary or "")
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _row_to_catalog(r: dict) -> SessionCatalogRow:
    """Convert a raw LanceDB sessions row to a SessionCatalogRow."""
    models_raw = r.get("models") or []
    if isinstance(models_raw, str):
        try:
            models_raw = json.loads(models_raw)
        except Exception:
            models_raw = []

    return SessionCatalogRow(
        id=r["id"],
        tool=r["tool"],
        title=r["title"],
        category=r["category"],
        author=r.get("author") or None,
        project=r.get("project") or None,
        visibility=r["visibility"],
        message_count=int(r.get("message_count", 0)),
        models=list(models_raw),
        preview=r.get("preview", ""),
        created_at=r.get("created_at", ""),
        updated_at=r.get("updated_at") or None,
        blob_uri=r.get("blob_uri", ""),
        summary=r.get("summary") or None,
        tokens_input=int(r.get("tokens_input") or 0),
        tokens_output=int(r.get("tokens_output") or 0),
        tokens_total=int(r.get("tokens_total") or 0),
    )


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
      0. Compute content_hash; if identical to stored hash → skip (idempotent).
      1. Optionally re-redact text fields if body.redacted is False.
      2. Store raw JSON in blob store (S3 or local).
      3. Build text chunks (summary + sliding window over messages).
      4. Embed chunks.
      5. Atomic upsert chunks + catalog row into LanceDB (merge_insert).
    """
    session = body.session
    now = datetime.now(timezone.utc).isoformat()

    # Determine effective summary early (needed for hash computation)
    effective_summary: Optional[str] = body.summary
    # Note: we do NOT call summarize_session here — that is sync-heavy and will
    # be moved to the jobs subsystem in Task 5. If no summary is provided we
    # fall back to a stub/empty so ingest stays fast.
    if not effective_summary:
        effective_summary = summarize_session(session, settings)

    # 0. Idempotency check
    content_hash = _compute_content_hash(body, effective_summary)
    vectors = get_vector_store(embedding_dim=get_embedder().dim)
    existing = vectors.get_session(session.id)

    if existing and existing.get("content_hash") == content_hash:
        # Identical content — skip re-embedding and re-writing
        return IngestResponse(
            session_id=session.id,
            blob_uri=existing.get("blob_uri", ""),
            chunks_indexed=0,
            summary_used=bool(existing.get("summary")),
            skipped=True,
            created_at=existing.get("created_at", now),
            updated_at=existing.get("updated_at", now),
        )

    # Preserve original created_at on re-ingest with new content
    original_created_at = existing.get("created_at", now) if existing else now

    # 1. Optional server-side redaction (belt-and-suspenders)
    if not body.redacted:
        for msg in session.messages:
            msg.text, _ = redact_text(msg.text)
            if msg.thinking:
                msg.thinking, _ = redact_text(msg.thinking)

    # 2. Persist raw JSON
    blob = get_blob_store()
    raw_json = body.model_dump_json()
    blob_uri = blob.put_session(
        author_id=body.author.id,
        session_id=session.id,
        raw_json=raw_json,
    )

    # 3. Build chunks
    chunks = build_chunks(session, summary=effective_summary)

    # 4. Embed
    embedder = get_embedder()
    texts = [c.text for c in chunks]
    vectors_list = embedder.embed_texts(texts) if texts else []

    # 5. Atomic upsert into LanceDB
    tokens_input = session.tokens.input if session.tokens else 0
    tokens_output = session.tokens.output if session.tokens else 0
    tokens_total = tokens_input + tokens_output

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
            "created_at": original_created_at,
        }
        for chunk, vec in zip(chunks, vectors_list)
    ]
    vectors.upsert_chunks(chunk_rows)

    # Refresh FTS index so new chunks are immediately searchable via keyword/hybrid modes.
    # This is intentionally done after each ingest (rather than per-query) to keep search
    # requests fast.  For high-throughput batch ingests, callers can call ensure_fts_index
    # directly after the batch rather than relying on per-ingest refresh.
    try:
        vectors.ensure_fts_index()
    except Exception:
        logger.warning("FTS index refresh failed after ingest of session %s — keyword search may be stale", session.id)

    catalog_row = {
        "id": session.id,
        "tool": session.tool,
        "title": session.title,
        "category": body.category,
        "author": body.author.id,
        "project": session.project or "",
        "visibility": body.visibility,
        "message_count": session.message_count,
        "models": list(session.models),
        "preview": session.preview,
        "created_at": original_created_at,
        "updated_at": now,
        "blob_uri": blob_uri,
        "summary": effective_summary or "",
        "content_hash": content_hash,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "tokens_total": tokens_total,
    }
    vectors.upsert_session(catalog_row)

    logger.info("Ingested session %s — %d chunks", session.id, len(chunks))
    return IngestResponse(
        session_id=session.id,
        blob_uri=blob_uri,
        chunks_indexed=len(chunks),
        summary_used=bool(effective_summary),
        skipped=False,
        created_at=original_created_at,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Session catalog
# ---------------------------------------------------------------------------

@router.get("/v1/sessions", response_model=SessionPage, tags=["sessions"])
def list_sessions(
    category: Optional[str] = Query(None),
    tool: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
    author: Optional[str] = Query(None),
    visibility: Optional[str] = Query(None),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    _token: str = Depends(require_api_key),
):
    """Return a paginated, sorted catalog page.

    Query params:
      limit   — page size (1–200, default 50)
      offset  — number of rows to skip (default 0)
      sort    — one of: created_at | updated_at | message_count | tokens_input |
                        tokens_output | tokens_total | project | tool | title
      order   — asc | desc (default desc)
    """
    if sort not in SORT_FIELDS:
        sort = "created_at"

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
    result = vectors.list_sessions(
        filters=filters or None,
        limit=limit,
        offset=offset,
        sort=sort,
        order=order,
    )

    items = [_row_to_catalog(r) for r in result["items"]]
    return SessionPage(
        items=items,
        total=result["total"],
        limit=result["limit"],
        offset=result["offset"],
    )


@router.get("/v1/sessions/{session_id}", response_model=SessionDetail, tags=["sessions"])
def get_session(
    session_id: str,
    _token: str = Depends(require_api_key),
):
    """Return SessionDetail (catalog row + raw blob JSON) for a specific session."""
    vectors = get_vector_store()
    row = vectors.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    # Fetch raw blob
    blob = get_blob_store()
    author_id = row.get("author", "")
    raw = blob.get_session(author_id=author_id, session_id=session_id)

    catalog = _row_to_catalog(row)
    return SessionDetail(
        catalog=catalog,
        raw=json.loads(raw) if raw else None,
    )


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
