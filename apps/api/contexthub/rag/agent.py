"""Company-wide RAG agent.

Pipeline:
  1. Embed the user's question.
  2. Vector-search the chunks table (with optional metadata filters).
  3. Assemble a numbered context with provenance (session id, title, tool).
  4. Ask Claude to answer citing the context as [n].
  5. Return QueryResponse with answer + deduplicated citations.

Falls back gracefully when no API key is present: returns the top-k
snippets concatenated, with a clear note that no Claude key is configured.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from contexthub.config import Settings
from contexthub.embeddings import Embedder
from contexthub.llm import LLMError, get_llm
from contexthub.models import Citation, QueryRequest, QueryResponse
from contexthub.storage.vectors import VectorStore

logger = logging.getLogger(__name__)

_SNIPPET_LEN = 200  # characters per citation snippet

_SYSTEM_PROMPT = """\
You are the company-wide Context Hub agent. Your knowledge comes exclusively
from the AI coding-assistant session excerpts provided below.

Rules:
1. Answer ONLY from the provided excerpts; do not use outside knowledge.
2. Cite your sources inline as [1], [2], etc., matching the excerpt numbers.
3. If the answer cannot be found in the excerpts, say so clearly rather than
   guessing.
4. Be concise and factual. Use markdown formatting where helpful.
"""


def _build_context_block(results: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Format search results into a numbered context block for Claude.

    Returns (context_string, list_of_provenance_dicts).
    """
    lines: list[str] = []
    provenance: list[dict[str, Any]] = []

    for i, row in enumerate(results, start=1):
        text = (row.get("text") or "").strip()
        session_id = row.get("session_id", "")
        # Prefer the fused RRF score from hybrid search (_score field).
        # Fall back to converting an L2 _distance to a similarity score.
        if "_score" in row:
            score = float(row["_score"])
        else:
            distance = float(row.get("_distance", 0.0))
            score = 1.0 / (1.0 + distance)
        lines.append(
            f"[{i}] Session: {session_id} | Tool: {row.get('tool', '')} "
            f"| Category: {row.get('category', '')} | Project: {row.get('project', '')}\n"
            f"{text}"
        )
        provenance.append({
            "session_id": session_id,
            "tool": row.get("tool", ""),
            "author": row.get("author"),
            "text": text,
            "score": score,
        })

    return "\n\n---\n\n".join(lines), provenance


def _build_citations(
    provenance: list[dict[str, Any]],
    session_titles: dict[str, str],
) -> list[Citation]:
    """Deduplicate by session_id and build Citation objects."""
    seen: dict[str, Citation] = {}
    for p in provenance:
        sid = p["session_id"]
        if sid not in seen:
            seen[sid] = Citation(
                session_id=sid,
                title=session_titles.get(sid, sid),
                tool=p["tool"],
                author=p.get("author"),
                snippet=p["text"][:_SNIPPET_LEN],
                score=p["score"],
            )
    return list(seen.values())


def answer_query(
    req: QueryRequest,
    vectors: VectorStore,
    embedder: Embedder,
    settings: Settings,
    caller_user_id: Optional[str] = None,
    caller_team: Optional[str] = None,
) -> QueryResponse:
    """Execute a RAG query and return a grounded answer with citations.

    Args:
        req:             The incoming query request (question + filters + top_k).
        vectors:         Initialised VectorStore.
        embedder:        Initialised embedder matching the stored vector dimension.
        settings:        Application settings (API key, model name, etc.).
        caller_user_id:  Authenticated caller's user_id (for visibility enforcement).
        caller_team:     Authenticated caller's team (for visibility enforcement).

    Returns:
        QueryResponse with answer and citations.  Only sessions the caller is
        authorised to see are included.
    """
    # 1. Embed the question
    query_vec = embedder.embed_query(req.question)

    # 2. Build metadata filter dict for the vector search
    raw_filters: dict[str, Any] = {}
    if req.filters:
        if req.filters.category:
            raw_filters["category"] = req.filters.category
        if req.filters.tool:
            raw_filters["tool"] = req.filters.tool
        if req.filters.project:
            raw_filters["project"] = req.filters.project
        if req.filters.author:
            raw_filters["author"] = req.filters.author

    # 3. Search — use hybrid (FTS + vector + RRF) by default; visibility enforced
    results = vectors.hybrid_search(
        query=req.question,
        query_vec=query_vec,
        top_k=req.top_k,
        filters=raw_filters or None,
        mode=req.mode,
        caller_user_id=caller_user_id,
        caller_team=caller_team,
    )

    if not results:
        return QueryResponse(
            answer="No relevant session excerpts found for your query.",
            citations=[],
        )

    # 4. Fetch titles for the matched sessions (for citation display)
    session_ids = list({r.get("session_id", "") for r in results})
    session_titles: dict[str, str] = {}
    for sid in session_ids:
        row = vectors.get_session(sid)
        if row:
            session_titles[sid] = row.get("title", sid)

    context_block, provenance = _build_context_block(results)
    citations = _build_citations(provenance, session_titles)

    def _stub(note: str) -> str:
        return (
            f"{note}\n\n"
            + "\n\n---\n\n".join(
                f"[{i}] {p['text'][:_SNIPPET_LEN]}" for i, p in enumerate(provenance, 1)
            )
        )

    # 5. Ask the configured LLM (default: the user's local `claude` CLI).
    try:
        llm = get_llm(settings, provider_override=req.provider, model_override=req.model)
        if not llm.available():
            return QueryResponse(
                answer=_stub("**Note:** No LLM provider available — showing raw excerpts."),
                citations=citations,
            )
        user_content = f"Question: {req.question}\n\nSession excerpts:\n\n{context_block}"
        answer = llm.complete(_SYSTEM_PROMPT, user_content, max_tokens=2048)
    except LLMError as exc:
        logger.warning("RAG LLM call failed: %s", exc)
        answer = _stub(f"**Note:** LLM call failed ({exc}) — showing raw excerpts.")

    return QueryResponse(answer=answer, citations=citations)
