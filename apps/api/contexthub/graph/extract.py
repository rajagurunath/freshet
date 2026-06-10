"""LLM-driven knowledge-graph extraction (Task 13).

Given a session's summary (preferred) or transcript, ask the LLM to return a
strict JSON object::

    {
      "nodes": [{"kind": "...", "name": "...", "summary": "..."}],
      "edges": [{"src": "<node name>", "dst": "<node name>", "rel": "..."}]
    }

We validate, normalize names (lowercase + trim), and upsert into the GraphStore
with dedup by (kind, name).  Every node/edge keeps the originating session_id as
provenance, so the same feature mentioned by two sessions becomes one shared node
linking both (cross-microservice / marketing linkage).

The extraction is best-effort: malformed JSON or an unavailable LLM yields an
empty result rather than raising, so it never blocks the job queue.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from contexthub.llm import LLMError, get_llm
from contexthub.models import NormalizedSession

logger = logging.getLogger(__name__)

# Allowed node kinds (per plan): repo|service|feature|person|decision|tool|pr.
ALLOWED_KINDS = {"repo", "service", "feature", "person", "decision", "tool", "pr"}

_TRANSCRIPT_FALLBACK_CHARS = 6_000

_SYSTEM_PROMPT = """\
You extract a small knowledge graph from a software session summary.

Return ONLY a JSON object with this exact shape (no prose, no markdown fence):
{
  "nodes": [{"kind": "<one of: repo|service|feature|person|decision|tool|pr>",
             "name": "<short canonical name>",
             "summary": "<one short phrase>"}],
  "edges": [{"src": "<a node name above>", "dst": "<a node name above>",
             "rel": "<short verb, e.g. implements, depends_on, worked_on, decided>"}]
}

Rules:
- Only include entities that clearly appear in the input.
- Use the canonical short name (e.g. "checkout", not "the checkout feature").
- Edge src/dst MUST be names that appear in the nodes list.
- Keep it small: at most ~12 nodes and ~12 edges.
- If nothing graph-worthy is present, return {"nodes": [], "edges": []}.
"""


def _build_input(session: NormalizedSession, summary: Optional[str]) -> str:
    """Prefer the summary; fall back to the first N chars of the transcript."""
    if summary and summary.strip():
        return summary.strip()
    parts: list[str] = []
    total = 0
    for msg in session.messages:
        text = (msg.text or "").strip()
        if not text:
            continue
        line = f"[{msg.role}] {text}\n"
        if total + len(line) > _TRANSCRIPT_FALLBACK_CHARS:
            break
        parts.append(line)
        total += len(line)
    return "".join(parts) or (session.title or session.preview or "")


def _parse_json(raw: str) -> Optional[dict[str, Any]]:
    """Best-effort parse of an LLM response into the expected dict.

    Tolerates code fences and leading/trailing prose by extracting the first
    balanced JSON object.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip a ```json ... ``` fence if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    # Fallback: grab the outermost {...}.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            return None
    return None


def extract_graph(
    session: NormalizedSession,
    summary: Optional[str],
    store: Any,
    llm: Optional[Any] = None,
    settings: Optional[Any] = None,
    visibility: str = "company",
    author: Optional[str] = None,
    team: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Extract nodes/edges from a session and upsert them into ``store``.

    Args:
        session:     The session being processed (for id + transcript fallback).
        summary:     The session summary (preferred extraction input).
        store:       A GraphStore instance.
        llm:         Optional pre-built LLM client (tests inject a stub).  When
                     None, one is built from ``settings`` / provider / model.
        visibility:  Visibility carried onto every extracted node.
        author/team: Owner identity for private/team-scoped nodes.

    Returns:
        {"session_id", "nodes_upserted", "edges_upserted"} — counts of rows
        actually written.  Best-effort: never raises on bad LLM output.
    """
    text = _build_input(session, summary)
    if not text.strip():
        return {"session_id": session.id, "nodes_upserted": 0, "edges_upserted": 0}

    if llm is None:
        llm = get_llm(settings, provider_override=provider, model_override=model)

    try:
        if not llm.available():
            logger.info("graph extract: LLM unavailable for session %s", session.id)
            return {"session_id": session.id, "nodes_upserted": 0, "edges_upserted": 0}
        raw = llm.complete(_SYSTEM_PROMPT, f"Input:\n{text}", max_tokens=1024)
    except LLMError as exc:
        logger.warning("graph extract: LLM call failed for %s: %s", session.id, exc)
        return {"session_id": session.id, "nodes_upserted": 0, "edges_upserted": 0}

    obj = _parse_json(raw)
    if not obj:
        logger.warning("graph extract: could not parse JSON for session %s", session.id)
        return {"session_id": session.id, "nodes_upserted": 0, "edges_upserted": 0}

    raw_nodes = obj.get("nodes") or []
    raw_edges = obj.get("edges") or []

    # name (normalized) → node id, for resolving edge endpoints.
    name_to_id: dict[str, str] = {}
    nodes_upserted = 0

    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        kind = str(n.get("kind", "")).strip().lower()
        name = str(n.get("name", "")).strip()
        if not name:
            continue
        if kind not in ALLOWED_KINDS:
            # Unknown kind → bucket as a generic "feature" rather than dropping.
            kind = "feature"
        norm = name.lower()
        try:
            node_id = store.upsert_node(
                kind=kind,
                name=name,
                session_id=session.id,
                visibility=visibility,
                summary=(str(n.get("summary")).strip() if n.get("summary") else None),
                author=author,
                team=team,
            )
        except ValueError:
            continue
        name_to_id[norm] = node_id
        nodes_upserted += 1

    edges_upserted = 0
    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        src_name = str(e.get("src", "")).strip().lower()
        dst_name = str(e.get("dst", "")).strip().lower()
        rel = str(e.get("rel", "")).strip()
        src_id = name_to_id.get(src_name)
        dst_id = name_to_id.get(dst_name)
        if not src_id or not dst_id or not rel:
            # Edge references an endpoint not in the nodes list → skip.
            continue
        try:
            store.upsert_edge(src=src_id, dst=dst_id, rel=rel, session_id=session.id)
            edges_upserted += 1
        except ValueError:
            continue

    logger.info(
        "graph extract: session %s → %d nodes, %d edges",
        session.id, nodes_upserted, edges_upserted,
    )
    return {
        "session_id": session.id,
        "nodes_upserted": nodes_upserted,
        "edges_upserted": edges_upserted,
    }
