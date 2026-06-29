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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from contexthub.config import Settings, get_settings
from contexthub.deps import Caller, optional_api_key, require_api_key
from contexthub.embeddings import get_embedder
from contexthub.ingest.chunker import build_chunks
from contexthub.ingest.redact import redact_text
from contexthub.models import (
    AssetMetadata,
    AssetPage,
    BatchSummarizeRequest,
    BatchSummarizeResponse,
    IngestRequest,
    IngestResponse,
    Job,
    QueryRequest,
    QueryResponse,
    Rule,
    RulePage,
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
    _caller: Caller = Depends(require_api_key),
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
        team=r.get("team") or None,
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
    request: Request,
    summarize: bool = Query(False, description="When true, enqueue an async summarization job instead of summarizing inline."),
    caller: Caller = Depends(require_api_key),
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

    When summarize=true the endpoint does NOT call summarize_session() inline.
    Instead it enqueues a 'summarize_session' job and returns immediately with
    job_id set.  The background worker will later update the catalog row's
    summary field.
    """
    session = body.session
    now = datetime.now(timezone.utc).isoformat()

    # Determine effective summary early (needed for hash computation)
    effective_summary: Optional[str] = body.summary
    # When summarize=true we skip the inline (blocking) summarization and
    # enqueue a background job instead.  This keeps the request path fast.
    if not effective_summary and not summarize:
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
            job_id=None,
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

    author_team = body.author.team or ""

    chunk_rows = [
        {
            "id": chunk.id,
            "session_id": session.id,
            "tool": session.tool,
            "category": body.category,
            "author": body.author.id,
            "team": author_team,
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

    # Serialize links from the session for link-based filtering (Task 16).
    links_json = json.dumps(
        [lnk.model_dump(mode="json") for lnk in session.links]
    )

    catalog_row = {
        "id": session.id,
        "tool": session.tool,
        "title": session.title,
        "category": body.category,
        "author": body.author.id,
        "team": author_team,
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
        "links_json": links_json,
    }
    vectors.upsert_session(catalog_row)

    # Enqueue background summarization if requested
    enqueued_job_id: Optional[str] = None
    job_store = getattr(request.app.state, "job_store", None)
    if summarize:
        try:
            enqueued_job_id = job_store.enqueue(
                kind="summarize_session",
                payload={"session_json": body.session.model_dump_json()},
            )
            logger.info("Enqueued summarize_session job %s for session %s", enqueued_job_id, session.id)
        except Exception:
            logger.exception("Failed to enqueue summarize_session job for session %s", session.id)

    # Enqueue knowledge-graph extraction off the request path (Task 13).
    if job_store is not None:
        try:
            graph_job_id = job_store.enqueue(
                kind="graph_extract",
                payload={"session_id": session.id},
            )
            logger.info("Enqueued graph_extract job %s for session %s", graph_job_id, session.id)
        except Exception:
            logger.exception("Failed to enqueue graph_extract job for session %s", session.id)

    logger.info("Ingested session %s — %d chunks", session.id, len(chunks))
    return IngestResponse(
        session_id=session.id,
        blob_uri=blob_uri,
        chunks_indexed=len(chunks),
        summary_used=bool(effective_summary),
        skipped=False,
        created_at=original_created_at,
        updated_at=now,
        job_id=enqueued_job_id,
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
    link: Optional[str] = Query(None, description="Filter sessions whose links[] contains this URL (Task 16)."),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    caller: Caller = Depends(require_api_key),
):
    """Return a paginated, sorted catalog page.

    Query params:
      limit   — page size (1–200, default 50)
      offset  — number of rows to skip (default 0)
      sort    — one of: created_at | updated_at | message_count | tokens_input |
                        tokens_output | tokens_total | project | tool | title
      order   — asc | desc (default desc)

    Visibility is enforced per the caller's identity:
      company-wide sessions are visible to all authenticated callers;
      team-scoped sessions are visible only to callers on the same team;
      private sessions are visible only to the owning user.
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
        caller_user_id=caller.user_id,
        caller_team=caller.team,
        link_url=link or None,
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
    caller: Caller = Depends(require_api_key),
):
    """Return SessionDetail (catalog row + raw blob JSON) for a specific session.

    Returns 404 when the session does not exist OR when the caller is not
    authorised to view it (i.e. visibility enforcement — private sessions
    appear as missing to other callers).
    """
    vectors = get_vector_store()
    row = vectors.get_session(
        session_id,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
        enforce_visibility=True,
    )
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
# PR context links (Task 16)
# ---------------------------------------------------------------------------

@router.post("/v1/sessions/{session_id}/share", tags=["context"])
def share_session(
    session_id: str,
    caller: Caller = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    request: Request = None,
):
    """Mint a short-lived HMAC share token for a session context page.

    Returns ``{url, token}`` where url points to GET /c/{session_id}?t=<token>&expiry=<n>.
    The context page is token-gated — no Bearer auth required, suitable for
    sharing with PR reviewers who do not have a hub account.

    The calling user must be able to see the session (visibility enforced).
    """
    from contexthub.api.context_page import sign_share_token

    vectors = get_vector_store()
    row = vectors.get_session(
        session_id,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
        enforce_visibility=True,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")

    # Never mint a forgeable share URL signed with the public default secret.
    settings.require_secure_token_secrets()
    secret = settings.share_token_secret
    token, expiry = sign_share_token(session_id, secret, ttl_seconds=86400)

    # Build the context page URL.  In production the base URL comes from the
    # request's base URL; in tests Request may be None — use a relative path.
    base_url = ""
    if request is not None:
        base_url = str(request.base_url).rstrip("/")

    url = f"{base_url}/c/{session_id}?t={token}&expiry={expiry}"
    return {"url": url, "token": token, "expiry": expiry}


@router.get("/c/{session_id}", tags=["context"], include_in_schema=False)
def context_page(
    session_id: str,
    t: Optional[str] = Query(None, description="HMAC share token"),
    expiry: Optional[int] = Query(None, description="Token expiry (UNIX epoch)"),
    settings: Settings = Depends(get_settings),
):
    """Render a token-gated HTML context page for a session.

    No Bearer auth — callers supply ?t=<token>&expiry=<n> from the share URL.
    Returns a self-contained HTML page showing the session title, summary
    ("why this PR"), PR/issue links, and a decision timeline with tool
    messages collapsed.

    Returns 403 when the token is missing, expired, or invalid.
    """
    from fastapi.responses import HTMLResponse

    from contexthub.api.context_page import render_context_page, verify_share_token

    # Validate token before doing anything else
    if not t or expiry is None:
        raise HTTPException(status_code=403, detail="Missing or invalid share token.")

    if not verify_share_token(session_id, t, expiry, settings.share_token_secret):
        raise HTTPException(status_code=403, detail="Invalid or expired share token.")

    # Fetch the session (no visibility enforcement — token is the gate)
    vectors = get_vector_store()
    row = vectors.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="Session not found.")

    # Fetch raw blob to get messages and links
    blob = get_blob_store()
    author_id = row.get("author", "")
    raw_bytes = blob.get_session(author_id=author_id, session_id=session_id)

    messages: list[dict] = []
    links: list[dict] = []
    if raw_bytes:
        try:
            raw_data = json.loads(raw_bytes)
            session_data = raw_data.get("session", {})
            messages = [
                {
                    "role": m.get("role", ""),
                    "text": m.get("text", ""),
                    "tool_name": m.get("tool_name"),
                }
                for m in session_data.get("messages", [])
            ]
            links = [
                {"kind": lnk.get("kind", "link"), "url": lnk.get("url", ""), "label": lnk.get("label")}
                for lnk in session_data.get("links", [])
            ]
        except Exception:
            logger.exception("Failed to parse raw blob for context page session %s", session_id)

    # Fall back to links_json from the catalog row if blob is unavailable
    if not links:
        try:
            links = json.loads(row.get("links_json") or "[]")
        except Exception:
            links = []

    # Optionally pull graph neighbors (best-effort; no error if graph is unavailable)
    graph_neighbors: list[dict] = []
    try:
        from contexthub.graph.store import get_graph_store
        gstore = get_graph_store()
        sub = gstore.session_subgraph(session_id)
        graph_neighbors = [{"name": n.get("name", ""), "kind": n.get("kind", "")} for n in sub.get("nodes", [])]
    except Exception:
        pass  # Graph is optional — do not fail the page render

    html = render_context_page(
        session_id=session_id,
        title=row.get("title", "Untitled session"),
        author=row.get("author", ""),
        summary=row.get("summary") or None,
        messages=messages,
        links=links,
        graph_neighbors=graph_neighbors,
        pr_url=None,
        created_at=row.get("created_at"),
    )
    return HTMLResponse(content=html, status_code=200)


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

@router.post("/v1/summarize", response_model=SummarizeResponse, tags=["rag"])
def summarize(
    body: SummarizeRequest,
    _caller: Caller = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """Generate a structured summary for a session (does not ingest it)."""
    summary = summarize_session(body.session, settings, provider=body.provider, model=body.model)
    return SummarizeResponse(summary=summary)


# ---------------------------------------------------------------------------
# Batch summarize
# ---------------------------------------------------------------------------

@router.post("/v1/summarize/batch", response_model=BatchSummarizeResponse, tags=["rag"])
def summarize_batch(
    body: BatchSummarizeRequest,
    request: Request,
    caller: Caller = Depends(require_api_key),
):
    """Enqueue a batch summarization job for multiple sessions.

    provider options:
      openai-batch  — upload to OpenAI Batch API (cheap, ~24h turnaround).
      local         — use a local openai-compatible server (e.g. Ollama).
      default       — use the hub's configured default LLM provider.

    Returns immediately with a job_id.  Poll GET /v1/jobs/{job_id} for status.
    """
    job_store = request.app.state.job_store
    job_id = job_store.enqueue(
        kind="summarize_batch",
        payload={
            "session_ids": body.session_ids,
            "provider": body.provider,
            "model": body.model,
        },
    )
    logger.info(
        "Enqueued summarize_batch job %s for %d sessions (provider=%s)",
        job_id,
        len(body.session_ids),
        body.provider,
    )
    return BatchSummarizeResponse(
        job_id=job_id,
        kind="summarize_batch",
        session_count=len(body.session_ids),
    )


# ---------------------------------------------------------------------------
# RAG query
# ---------------------------------------------------------------------------

@router.post("/v1/query", response_model=QueryResponse, tags=["rag"])
def query(
    body: QueryRequest,
    caller: Caller = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """RAG query across all ingested sessions.

    Visibility is enforced: the caller will only receive citations from
    sessions they are authorised to read.
    """
    embedder = get_embedder()
    vectors = get_vector_store(embedding_dim=embedder.dim)
    return answer_query(
        req=body,
        vectors=vectors,
        embedder=embedder,
        settings=settings,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/v1/stats", response_model=StatsResponse, tags=["meta"])
def stats(
    _caller: Caller = Depends(require_api_key),
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


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def _job_row_to_model(row: dict) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        payload=row.get("payload") or {},
        status=row["status"],
        result=row.get("result"),
        error=row.get("error"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        scheduled_for=row.get("scheduled_for"),
    )


@router.get("/v1/jobs/{job_id}", response_model=Job, tags=["jobs"])
def get_job(
    job_id: str,
    request: Request,
    _caller: Caller = Depends(require_api_key),
):
    """Return a single job record by id."""
    job_store = request.app.state.job_store
    row = job_store.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return _job_row_to_model(row)


@router.get("/v1/jobs", response_model=list[Job], tags=["jobs"])
def list_jobs(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status (queued|running|done|error)"),
    kind: Optional[str] = Query(None, description="Filter by job kind"),
    limit: int = Query(200, ge=1, le=1000),
    _caller: Caller = Depends(require_api_key),
):
    """Return a list of job records, optionally filtered by status and/or kind."""
    job_store = request.app.state.job_store
    rows = job_store.list(status=status, kind=kind, limit=limit)
    return [_job_row_to_model(r) for r in rows]


# ---------------------------------------------------------------------------
# Harvest status (Task 12)
# ---------------------------------------------------------------------------

@router.get("/v1/harvest/status", tags=["harvest"])
def harvest_status(
    _caller: Caller = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """Return the subscription-window harvester status.

    Reports:
      harvest_enabled    — whether the harvester is active
      next_reset         — ISO-8601 UTC datetime of the next window reset
      pending_counts     — number of sessions lacking summaries / graph extraction
      last_drain_results — result dict from the most recent completed harvest_check job
    """
    from contexthub.jobs.harvest import get_harvest_status
    return get_harvest_status(settings)


# ---------------------------------------------------------------------------
# Knowledge graph (Task 13)
# ---------------------------------------------------------------------------

def _build_graph_response(store, nodes: list[dict], edges: list[dict]) -> "GraphResponse":
    """Attach session provenance and convert raw rows to a GraphResponse."""
    from contexthub.models import GraphEdge, GraphNode, GraphResponse

    out_nodes = [
        GraphNode(
            id=n["id"],
            kind=n["kind"],
            name=n["name"],
            summary=n.get("summary"),
            visibility=n.get("visibility"),
            session_ids=store.sessions_for_node(n["id"]),
        )
        for n in nodes
    ]
    out_edges = [
        GraphEdge(
            id=e["id"], src=e["src"], dst=e["dst"], rel=e["rel"],
            weight=float(e.get("weight", 1.0)), session_id=e.get("session_id"),
        )
        for e in edges
    ]
    return GraphResponse(nodes=out_nodes, edges=out_edges)


@router.get("/v1/graph", tags=["graph"])
def get_graph(
    focus: Optional[str] = Query(None, description="Focus node name; returns its depth-hop neighborhood."),
    depth: int = Query(1, ge=0, le=4),
    caller: Caller = Depends(require_api_key),
):
    """Return the knowledge graph, optionally focused on a node's neighborhood.

    Without ``focus`` the full visible graph is returned (nodes + edges).
    With ``focus`` we match the term against node names and return the union of
    each match's ``depth``-hop neighborhood.  Visibility is enforced: graph rows
    carry the visibility of the session that produced them.
    """
    from contexthub.graph.store import get_graph_store
    from contexthub.models import GraphResponse

    store = get_graph_store()

    if not focus:
        nodes = store.list_nodes(caller_user_id=caller.user_id, caller_team=caller.team)
        edges = store.list_edges(caller_user_id=caller.user_id, caller_team=caller.team)
        return _build_graph_response(store, nodes, edges)

    # Focused: find matching nodes, union their neighborhoods.
    matches = store.find_nodes_by_terms(
        [focus], caller_user_id=caller.user_id, caller_team=caller.team
    )
    merged_nodes: dict[str, dict] = {}
    merged_edges: dict[str, dict] = {}
    for m in matches:
        sub = store.neighbors(
            m["id"], depth=depth,
            caller_user_id=caller.user_id, caller_team=caller.team,
        )
        for n in sub["nodes"]:
            merged_nodes[n["id"]] = n
        for e in sub["edges"]:
            merged_edges[e["id"]] = e
    return _build_graph_response(store, list(merged_nodes.values()), list(merged_edges.values()))


@router.post("/v1/graph/backfill", tags=["graph"])
def backfill_graph(
    request: Request,
    caller: Caller = Depends(require_api_key),
):
    """Enqueue graph_extract jobs for every catalog session not yet extracted.

    Iterates all sessions visible to the caller (same visibility rules as
    GET /v1/sessions) and enqueues a ``graph_extract`` job for each one whose
    ``graph_extracted`` flag is False.  Already-extracted sessions are skipped.

    Returns ``{enqueued: int, skipped: int}``.
    """
    vectors = get_vector_store()
    result = vectors.list_sessions(
        filters=None,
        limit=10_000,
        offset=0,
        sort="created_at",
        order="desc",
        caller_user_id=caller.user_id,
        caller_team=caller.team,
    )
    all_rows = result["items"]

    job_store = getattr(request.app.state, "job_store", None)
    enqueued = 0
    skipped = 0
    for row in all_rows:
        if row.get("graph_extracted"):
            skipped += 1
        else:
            if job_store is not None:
                try:
                    job_store.enqueue(
                        kind="graph_extract",
                        payload={"session_id": row["id"]},
                    )
                    enqueued += 1
                except Exception:
                    logger.exception(
                        "backfill: failed to enqueue graph_extract for session %s", row["id"]
                    )
            else:
                enqueued += 1  # no job store in test harness — still count it

    logger.info("Graph backfill: enqueued=%d skipped=%d", enqueued, skipped)
    return {"enqueued": enqueued, "skipped": skipped}


@router.post("/v1/graph/resolve-backfill", tags=["graph"])
def resolve_backfill_graph(
    request: Request,
    caller: Caller = Depends(require_api_key),
):
    """Run cross-session entity resolution over already-extracted sessions.

    Unlike ``/v1/graph/backfill`` (which *extracts* graphs), this *links* the
    graphs that already exist: it enqueues an ``entity_resolve`` job for every
    session whose graph has been extracted, creating reversible ``same_as`` edges
    between same-concept nodes that different sessions named differently
    (e.g. ``checkout`` ↔ ``payment-checkout``). Sessions without an extracted
    graph yet are skipped — run graph backfill for those first.

    Returns ``{enqueued: int, skipped: int}`` where ``skipped`` counts visible
    catalog sessions that have no graph yet (extract those first).
    """
    from contexthub.graph.store import get_graph_store

    # Sessions whose graph actually exists (by node provenance) — the real
    # resolution targets — unioned with catalog sessions flagged as extracted.
    store = get_graph_store()
    targets = set(store.session_ids_with_nodes())

    vectors = get_vector_store()
    result = vectors.list_sessions(
        filters=None,
        limit=10_000,
        offset=0,
        sort="created_at",
        order="desc",
        caller_user_id=caller.user_id,
        caller_team=caller.team,
    )
    catalog_rows = result["items"]
    for row in catalog_rows:
        if row.get("graph_extracted"):
            targets.add(row["id"])

    job_store = getattr(request.app.state, "job_store", None)
    enqueued = 0
    for sid in targets:
        if job_store is not None:
            try:
                job_store.enqueue(kind="entity_resolve", payload={"session_id": sid})
                enqueued += 1
            except Exception:
                logger.exception(
                    "resolve-backfill: failed to enqueue entity_resolve for session %s",
                    sid,
                )
        else:
            enqueued += 1  # no job store in test harness — still count it

    # Sessions visible in the catalog but with no graph yet — a hint to extract.
    skipped = sum(
        1
        for row in catalog_rows
        if not row.get("graph_extracted") and row["id"] not in targets
    )

    logger.info("Graph resolve-backfill: enqueued=%d skipped=%d", enqueued, skipped)
    return {"enqueued": enqueued, "skipped": skipped}


@router.get("/v1/graph/sessions", tags=["graph"])
def list_graph_sessions(caller: Caller = Depends(require_api_key)):
    """Return the ids of sessions that have a knowledge graph extracted.

    Lets the desktop filter its (disk-scanned) session list down to the ones the
    hub has already graphed.
    """
    from contexthub.graph.store import get_graph_store

    store = get_graph_store()
    return {"session_ids": store.session_ids_with_nodes()}


@router.get("/v1/graph/session/{session_id}", tags=["graph"])
def get_graph_for_session(
    session_id: str,
    caller: Caller = Depends(require_api_key),
):
    """Return the subgraph (nodes + edges) extracted from a single session."""
    from contexthub.graph.store import get_graph_store

    store = get_graph_store()
    sub = store.session_subgraph(
        session_id, caller_user_id=caller.user_id, caller_team=caller.team
    )
    return _build_graph_response(store, sub["nodes"], sub["edges"])


# ---------------------------------------------------------------------------
# Rules (Task 14)
# ---------------------------------------------------------------------------

def _row_to_rule(r: dict) -> Rule:
    """Convert a raw RulesStore dict to a Rule model."""
    return Rule(
        id=r["id"],
        text=r["text"],
        rationale=r.get("rationale"),
        evidence=r.get("evidence") or [],
        scope=r.get("scope"),
        status=r.get("status", "proposed"),
        author=r.get("author"),
        created_at=r.get("created_at", ""),
        updated_at=r.get("updated_at"),
    )


def _scoped_rules_author(caller: Caller, requested_author: Optional[str]) -> Optional[str]:
    """Resolve the author filter for a rules read, enforcing per-caller scoping.

    Rules carry rationale/evidence mined from an author's private/team sessions,
    so a caller may only read their own rules.  An identified caller is always
    scoped to their own ``user_id``; requesting another author's rules is
    forbidden.  Bare-key (anonymous) callers have no identity and so cannot
    read any author-scoped rules.
    """
    if caller.user_id is None:
        raise HTTPException(
            status_code=403,
            detail="An identified API key (key:user_id:team) is required to read rules.",
        )
    if requested_author is not None and requested_author != caller.user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot read rules for another author.",
        )
    return caller.user_id


@router.get("/v1/rules", response_model=RulePage, tags=["rules"])
def list_rules(
    status: Optional[str] = Query(None, description="Filter by status: proposed|accepted|rejected"),
    author: Optional[str] = Query(None, description="Filter by author user_id (defaults to caller)"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_api_key),
):
    """List the caller's extracted rules, optionally filtered by status.

    Rules are scoped to the caller's own ``user_id`` — they embed rationale and
    evidence mined from the author's private/team sessions and must not leak
    across users.  Rules are always in 'proposed' status when first extracted.
    The user must explicitly accept them via POST /v1/rules/{id}/accept.
    """
    from contexthub.rules.store import get_rules_store

    author = _scoped_rules_author(caller, author)
    store = get_rules_store()
    rows = store.list_rules(status=status, author=author, limit=limit, offset=offset)
    total = store.count_rules(status=status, author=author)
    return RulePage(
        items=[_row_to_rule(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/v1/rules/{rule_id}/accept", response_model=Rule, tags=["rules"])
def accept_rule(
    rule_id: str,
    _caller: Caller = Depends(require_api_key),
):
    """Accept a proposed rule (consent gate — explicit opt-in required).

    Once accepted, the rule appears in GET /v1/rules/export as a CLAUDE.md-style
    block.  Accepting is idempotent.
    """
    from contexthub.rules.store import get_rules_store

    store = get_rules_store()
    updated = store.set_status(rule_id, "accepted")
    if not updated:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    rule = store.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    return _row_to_rule(rule)


@router.post("/v1/rules/{rule_id}/reject", response_model=Rule, tags=["rules"])
def reject_rule(
    rule_id: str,
    _caller: Caller = Depends(require_api_key),
):
    """Reject a proposed rule.

    Rejected rules are not exported and will not be re-proposed in future
    extractions that check against existing texts.
    """
    from contexthub.rules.store import get_rules_store

    store = get_rules_store()
    updated = store.set_status(rule_id, "rejected")
    if not updated:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    rule = store.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    return _row_to_rule(rule)


@router.get("/v1/rules/export", tags=["rules"])
def export_rules(
    caller: Caller = Depends(require_api_key),
):
    """Export the caller's accepted rules as a CLAUDE.md-style markdown block.

    Only rules with status='accepted' that belong to the caller appear here —
    the consent gate is enforced server-side and rules are scoped to the
    caller's own author.  Paste the output into your project's CLAUDE.md.

    Returns plain text (Content-Type: text/markdown).
    """
    from fastapi.responses import PlainTextResponse

    from contexthub.rules.store import get_rules_store

    author = _scoped_rules_author(caller, None)
    store = get_rules_store()
    accepted = store.list_rules(status="accepted", author=author)

    if not accepted:
        return PlainTextResponse(
            "# Rules\n\n_No accepted rules yet. "
            "Accept proposed rules via POST /v1/rules/{id}/accept._\n",
            media_type="text/markdown",
        )

    lines: list[str] = [
        "# Rules",
        "",
        "> Generated by Context Hub — accepted rules only.",
        "",
    ]
    for rule in accepted:
        lines.append(f"- {rule['text']}")
        if rule.get("rationale"):
            lines.append(f"  _{rule['rationale']}_")
    lines.append("")

    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


@router.post("/v1/rules/mine", tags=["rules"])
def mine_rules(
    request: Request,
    author: Optional[str] = Query(None, description="Author user_id to mine (defaults to caller)"),
    n_sessions: int = Query(20, ge=1, le=100, description="Number of recent sessions to mine"),
    provider: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    caller: Caller = Depends(require_api_key),
):
    """Enqueue a rules extraction job for an author's recent sessions.

    Returns immediately with a job_id.  Poll GET /v1/jobs/{job_id} for status.
    The job runs rules_extract_handler in the background, which reads the
    author's last N session summaries and extracts recurring preferences.
    """
    effective_author = author or caller.user_id
    job_store = request.app.state.job_store
    job_id = job_store.enqueue(
        kind="rules_extract",
        payload={
            "author": effective_author,
            "n_sessions": n_sessions,
            "provider": provider,
            "model": model,
        },
    )
    logger.info(
        "Enqueued rules_extract job %s for author=%s, n_sessions=%d",
        job_id,
        effective_author,
        n_sessions,
    )
    return {"job_id": job_id, "kind": "rules_extract", "author": effective_author}


# ---------------------------------------------------------------------------
# Asset hub (Task 15)
# ---------------------------------------------------------------------------

def _row_to_asset_metadata(r: dict) -> AssetMetadata:
    """Convert an AssetStore dict to an AssetMetadata model."""
    return AssetMetadata(
        id=r["id"],
        kind=r["kind"],
        name=r["name"],
        description=r.get("description", ""),
        category=r.get("category", "general"),
        author=r.get("author", ""),
        team=r.get("team") or None,
        visibility=r.get("visibility", "company"),
        files=r.get("files") or [],
        blob_uri=r.get("blob_uri", ""),
        version=r.get("version", "1.0.0"),
        created_at=r.get("created_at", ""),
    )


@router.post("/v1/assets", response_model=AssetMetadata, tags=["assets"])
async def upload_asset(
    kind: str = Form(..., description="Asset kind: skill | script | config | prompt"),
    name: str = Form(..., description="Human-readable asset name"),
    description: str = Form("", description="Asset description (used for FTS search)"),
    category: str = Form("general", description="Asset category (used as OpenSharing schema)"),
    visibility: str = Form("company", description="company | team | private"),
    version: str = Form("1.0.0"),
    file: UploadFile = File(..., description="ZIP archive containing the asset files"),
    caller: Caller = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """Upload an asset (skill, script, config, or prompt) as a multipart ZIP.

    The ZIP payload is stored in the local blob directory (or S3 in production).
    Metadata is indexed in SQLite with FTS over name + description.

    Returns the created AssetMetadata record.
    """
    import json as _json

    from contexthub.assets.store import get_asset_store

    # Validate kind
    valid_kinds = ("skill", "script", "config", "prompt")
    if kind not in valid_kinds:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid kind '{kind}'. Must be one of: {', '.join(valid_kinds)}",
        )

    # Read the uploaded file
    data = await file.read()
    original_filename = file.filename or "payload.zip"

    store = get_asset_store()

    # Create a placeholder asset record to get an id, then store the blob
    asset_id = store.create_asset(
        kind=kind,
        name=name,
        description=description,
        category=category,
        author=caller.user_id or "anonymous",
        team=caller.team,
        visibility=visibility,
        files_json=_json.dumps([original_filename]),
        blob_uri="",   # will be updated after blob write
        version=version,
    )

    # Store the blob and get back the URI
    blob_uri = store.store_blob(asset_id, data, filename=original_filename)

    # Update the record with the real blob_uri
    with store._connect() as conn:
        conn.execute(
            "UPDATE assets SET blob_uri = ? WHERE id = ?",
            (blob_uri, asset_id),
        )
        conn.commit()

    asset = store.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve asset after creation.")

    logger.info("Uploaded asset %s (%s): %s", asset_id, kind, name)
    return _row_to_asset_metadata(asset)


@router.get("/v1/assets", response_model=AssetPage, tags=["assets"])
def list_assets(
    kind: Optional[str] = Query(None, description="Filter by kind: skill | script | config | prompt"),
    category: Optional[str] = Query(None, description="Filter by category"),
    q: Optional[str] = Query(None, description="Full-text search over name + description"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_api_key),
):
    """List assets with optional kind/category filter and FTS search.

    Visibility is enforced: private assets are only visible to their owner;
    team-scoped assets are visible only to callers on the same team.
    """
    from contexthub.assets.store import get_asset_store

    store = get_asset_store()
    items = store.list_assets(
        kind=kind,
        category=category,
        q=q,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
        limit=limit,
        offset=offset,
    )
    # Count without q for accurate pagination (FTS count is expensive; use filtered count)
    total = store.count_assets(
        kind=kind,
        category=category,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
    )
    return AssetPage(
        items=[_row_to_asset_metadata(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/v1/assets/{asset_id}", response_model=AssetMetadata, tags=["assets"])
def get_asset(
    asset_id: str,
    caller: Caller = Depends(require_api_key),
):
    """Return metadata for a single asset.

    Returns 404 when the asset does not exist OR when the caller is not
    authorised to view it (visibility enforcement).
    """
    from contexthub.assets.store import get_asset_store

    store = get_asset_store()
    asset = store.get_asset(
        asset_id,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
        enforce_visibility=True,
    )
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found.")
    return _row_to_asset_metadata(asset)


@router.get("/v1/assets/{asset_id}/download", tags=["assets"])
def download_asset(
    asset_id: str,
    token: Optional[str] = Query(None, description="HMAC signed download token"),
    expiry: Optional[int] = Query(None, description="Token expiry (UNIX epoch)"),
    caller: Optional[Caller] = Depends(optional_api_key),
    settings: Settings = Depends(get_settings),
):
    """Download an asset's ZIP payload.

    Two modes:
      1. Authenticated caller (Bearer token) — no signed token required;
         the caller's identity is used for the visibility check.
      2. Pre-signed download URL (token + expiry params) — used by the
         OpenSharing credential vending flow; the valid HMAC token is the
         authorization gate, so visibility is not enforced (and no Bearer
         token is required).

    At least one of the two credentials must be present.

    Returns the file content with Content-Disposition: attachment.
    """
    from fastapi.responses import FileResponse

    from contexthub.assets.store import get_asset_store, verify_download_token

    store = get_asset_store()

    # Validate the pre-signed token if provided (OpenSharing download-URL flow).
    token_ok = False
    if token and expiry is not None:
        if not verify_download_token(asset_id, token, expiry, settings.asset_token_secret):
            raise HTTPException(status_code=403, detail="Invalid or expired download token.")
        token_ok = True

    # Require at least one credential: a valid pre-signed token OR a Bearer caller.
    if not token_ok and caller is None:
        raise HTTPException(
            status_code=401,
            detail="A Bearer token or a valid signed download token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A valid pre-signed token is the authorization gate on its own, so skip
    # visibility enforcement in that branch.  Otherwise enforce visibility
    # against the authenticated caller's identity.
    asset = store.get_asset(
        asset_id,
        caller_user_id=caller.user_id if caller else None,
        caller_team=caller.team if caller else None,
        enforce_visibility=not token_ok,
    )
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset '{asset_id}' not found.")

    blob_path = store.get_blob_path(asset.get("blob_uri", ""))
    if blob_path is None or not blob_path.exists():
        raise HTTPException(status_code=404, detail="Asset payload not found in blob store.")

    filename = blob_path.name
    return FileResponse(
        path=str(blob_path),
        filename=filename,
        media_type="application/zip",
    )
