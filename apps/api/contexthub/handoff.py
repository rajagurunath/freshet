"""AICP handoff assembly — the hub side of the AI Context Protocol (§6).

All assembly logic lives here so the REST routes (``api/routes.py``) stay thin.
A session is resolved **disk-first** (live, possibly-unpushed transcripts via
``graph/build.py``) then from the **hub catalog** (LanceDB). The handoff bundle
reuses the knowledge graph (``graph/store.py``) for the working set + related
sessions, and runs **redaction** (``ingest/redact.py``) on every outbound text
field before any bytes leave.

Conventions:
  * camelCase on the wire (the Pydantic models alias-generate it).
  * Stable error set via :class:`AICPError` (``not_found`` / ``forbidden`` /
    ``consent_required`` / ``invalid_cursor`` / ``rate_limited`` / ``internal``).
  * Best-effort: graph/catalog lookups never raise; only the deliberate
    ``AICPError`` cases (not_found, consent_required, invalid_cursor) propagate.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Optional

from contexthub.ingest.redact import redact_text
from contexthub.models import (
    GrepMatch,
    GrepResponse,
    HandoffDecision,
    HandoffMore,
    HandoffPacket,
    HandoffRelatedSession,
    HandoffWorkingSet,
    Message,
    NormalizedMessage,
    RecentResponse,
    SessionManifest,
    SummaryResponse,
    TokenCounts,
)

ISSUED_BY = "freshet-local"

# ---------------------------------------------------------------------------
# Error type + status map (consumed by routes)
# ---------------------------------------------------------------------------

_AICP_STATUS = {
    "not_found": 404,
    "forbidden": 403,
    "consent_required": 403,
    "invalid_cursor": 400,
    "rate_limited": 429,
    "internal": 500,
}


class AICPError(Exception):
    """A protocol error carrying one of the stable AICP error codes."""

    def __init__(self, code: str, message: str, hint: Optional[str] = None) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(f"{code}: {message}")

    @property
    def status(self) -> int:
        return _AICP_STATUS.get(self.code, 500)

    def to_detail(self) -> dict:
        detail: dict = {"error": self.code, "message": self.message}
        if self.hint:
            detail["hint"] = self.hint
        return detail


# ---------------------------------------------------------------------------
# Consent gate (spec §8) — reads env directly so config.py stays untouched
# ---------------------------------------------------------------------------

def require_consent(agent: Optional[str]) -> None:
    """Raise ``consent_required`` unless the agent is granted access.

    ``FRESHET_CONSENT=allow`` (the v1 dev default) bypasses entirely. Otherwise
    the agent id (from the ``X-AICP-Agent`` header) must appear in the
    comma-separated ``FRESHET_CONSENT_GRANTS`` allowlist.
    """
    mode = os.environ.get("FRESHET_CONSENT", "allow").strip().lower()
    if mode == "allow":
        return
    grants = {
        a.strip()
        for a in os.environ.get("FRESHET_CONSENT_GRANTS", "").split(",")
        if a.strip()
    }
    if agent and agent in grants:
        return
    raise AICPError(
        "consent_required",
        f"Agent {agent or '(unknown)'} is not granted access to Freshet sessions.",
        hint="Set FRESHET_CONSENT=allow (dev) or add the agent id to FRESHET_CONSENT_GRANTS.",
    )


# ---------------------------------------------------------------------------
# Redaction helper
# ---------------------------------------------------------------------------

def _redact(text: Optional[str]) -> str:
    if not text:
        return text or ""
    clean, _ = redact_text(text)
    return clean


# ---------------------------------------------------------------------------
# Session resolution — disk-first (live/unpushed) then catalog
# ---------------------------------------------------------------------------

class _Resolved:
    """Resolution result: at least one of (sess, row) is populated."""

    def __init__(self) -> None:
        self.sess = None              # NormalizedSession (disk hit)
        self.disk_summary: str = ""
        self.path: Optional[str] = None
        self.kind: Optional[str] = None
        self.ts: Optional[str] = None
        self.row: Optional[dict] = None  # LanceDB catalog row
        self.messages: list[Message] = []


def resolve_disk(session_id: str):
    """Resolve a session by id from the on-disk transcripts (live/unpushed).

    Mirrors the resolve-by-id loop in ``routes.build_session_graph``. Returns
    ``(sess, summary, path, kind, ts)`` or ``None``.
    """
    try:
        from contexthub.graph.build import (
            list_session_files,
            parse_claude,
            parse_codex,
            to_session,
        )

        for path, kind in list_session_files():
            if os.path.splitext(os.path.basename(path))[0] == session_id:
                parsed = parse_claude(path) if kind == "claude" else parse_codex(path)
                if not parsed:
                    return None
                sess, summary = to_session(parsed)
                return sess, summary, path, kind, parsed.get("ts")
    except Exception:
        return None
    return None


def resolve_catalog(session_id: str, caller) -> Optional[dict]:
    """Resolve a session row from the hub catalog (visibility enforced)."""
    try:
        from contexthub.storage.vectors import get_vector_store

        return get_vector_store().get_session(
            session_id,
            caller_user_id=getattr(caller, "user_id", None),
            caller_team=getattr(caller, "team", None),
            enforce_visibility=True,
        )
    except Exception:
        return None


def _messages_from_catalog(row: dict, session_id: str) -> list[Message]:
    """Rebuild a Message list from the raw blob for a catalog-only session."""
    try:
        from contexthub.storage.blob import get_blob_store

        raw = get_blob_store().get_session(
            author_id=row.get("author", ""), session_id=session_id
        )
        if not raw:
            return []
        data = json.loads(raw)
        # The blob holds the full IngestRequest envelope: messages live under
        # ``session.messages``. Fall back to a top-level ``messages`` list.
        if isinstance(data.get("session"), dict):
            raw_msgs = data["session"].get("messages", [])
        else:
            raw_msgs = data.get("messages", [])
        out: list[Message] = []
        for i, m in enumerate(raw_msgs or []):
            try:
                out.append(
                    Message(
                        id=m.get("id") or f"{session_id}-{i}",
                        role=m.get("role", "user"),
                        text=m.get("text", "") or "",
                        thinking=m.get("thinking"),
                        tool_name=m.get("tool_name"),
                        timestamp=m.get("timestamp"),
                        model=m.get("model"),
                    )
                )
            except Exception:
                continue
        return out
    except Exception:
        return []


def resolve(session_id: str, caller) -> _Resolved:
    """Disk-first then catalog. Raise ``not_found`` if neither resolves."""
    r = _Resolved()
    disk = resolve_disk(session_id)
    if disk:
        r.sess, r.disk_summary, r.path, r.kind, r.ts = disk
        r.messages = list(r.sess.messages)
    r.row = resolve_catalog(session_id, caller)
    if r.row and not r.messages:
        r.messages = _messages_from_catalog(r.row, session_id)
    if r.sess is None and r.row is None:
        raise AICPError("not_found", f"Session '{session_id}' not found.")
    return r


# ---------------------------------------------------------------------------
# Manifest + summary
# ---------------------------------------------------------------------------

def to_manifest(r: _Resolved) -> SessionManifest:
    """Build a SessionManifest, preferring the richer catalog row."""
    row = r.row
    if row is not None:
        return SessionManifest(
            id=row["id"],
            tool=row.get("tool", "claude-code"),
            title=_redact(row.get("title", "")),
            project=_redact(row.get("project") or "") or None,
            started_at=row.get("created_at"),
            ended_at=row.get("updated_at"),
            message_count=int(row.get("message_count", 0) or 0),
            tokens=TokenCounts(
                input=int(row.get("tokens_input") or 0),
                output=int(row.get("tokens_output") or 0),
            ),
            has_summary=bool(row.get("summary")),
            visibility=row.get("visibility", "company"),
            source="hub",
        )
    sess = r.sess
    return SessionManifest(
        id=sess.id,
        tool=sess.tool,
        title=_redact(sess.title),
        project=_redact(sess.project or "") or None,
        started_at=sess.started_at or r.ts,
        ended_at=sess.ended_at,
        message_count=sess.message_count or len(r.messages),
        tokens=TokenCounts(input=0, output=0),
        has_summary=False,
        visibility="private",
        source="local",
    )


def compute_summary(r: _Resolved) -> SummaryResponse:
    """Prefer the richer catalog summary; fall back to the disk first-message."""
    row = r.row
    if row is not None and row.get("summary"):
        return SummaryResponse(
            summary=_redact(row["summary"]),
            generated_by="hub-catalog",
            generated_at=row.get("created_at"),
        )
    return SummaryResponse(
        summary=_redact(r.disk_summary or (r.sess.preview if r.sess else "")),
        generated_by="disk-first-message",
        generated_at=r.ts,
    )


# ---------------------------------------------------------------------------
# Touched files — the one new parsing pass (disk tool_use blocks)
# ---------------------------------------------------------------------------

_CLAUDE_FILE_TOOLS = {"Edit", "Write", "Read", "NotebookEdit", "MultiEdit"}


def extract_touched_files(path: Optional[str], kind: Optional[str]) -> list[str]:
    """Mine file paths from assistant tool_use blocks in a disk transcript.

    Claude: ``tool_use`` blocks named Edit/Write/Read/NotebookEdit/MultiEdit →
    ``input.file_path`` / ``input.notebook_path``. Codex: ``function_call``
    payloads → ``json.loads(arguments).file_path|path``. Bash / local_shell_call
    path mining is a documented fast-follow. Deduped, order-preserving, capped 50.
    """
    if not path or not kind:
        return []
    seen: list[str] = []

    def _add(fp) -> None:
        if isinstance(fp, str) and fp and fp not in seen:
            seen.append(fp)

    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if kind == "claude":
                    if o.get("type") != "assistant":
                        continue
                    content = (o.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        if b.get("name") in _CLAUDE_FILE_TOOLS:
                            inp = b.get("input") or {}
                            _add(inp.get("file_path") or inp.get("notebook_path"))
                else:  # codex
                    if o.get("type") != "response_item":
                        continue
                    payload = o.get("payload") or {}
                    if payload.get("type") == "function_call":
                        args = payload.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        if isinstance(args, dict):
                            _add(args.get("file_path") or args.get("path"))
                if len(seen) >= 50:
                    break
    except Exception:
        return seen[:50]
    return seen[:50]


# ---------------------------------------------------------------------------
# Working set / decisions / related sessions — from the knowledge graph
# ---------------------------------------------------------------------------

_WS_KIND_MAP = {"repo": "repos", "service": "services", "tool": "libraries"}


def working_set_and_decisions(session_id: str, caller):
    """Derive (HandoffWorkingSet, decisions) from the session subgraph."""
    ws = HandoffWorkingSet()
    decisions: list[HandoffDecision] = []
    try:
        from contexthub.graph.store import get_graph_store

        sub = get_graph_store().session_subgraph(
            session_id,
            caller_user_id=getattr(caller, "user_id", None),
            caller_team=getattr(caller, "team", None),
        )
    except Exception:
        return ws, decisions

    for node in sub.get("nodes", []):
        kind = node.get("kind")
        name = node.get("name")
        if not name:
            continue
        bucket = _WS_KIND_MAP.get(kind)
        if bucket:
            target = getattr(ws, bucket)
            if name not in target:
                target.append(name)
        elif kind == "decision":
            decisions.append(
                HandoffDecision(decision=_redact(name), why=_redact(node.get("summary")) or None)
            )
    return ws, decisions


def related_sessions(session_id: str, caller, cap: int = 5) -> list[HandoffRelatedSession]:
    """Graph-linked sessions: walk the session's nodes' 1-hop neighbours.

    For each node in the session subgraph, traverse ``neighbors(depth=1)``
    (which crosses ``same_as`` + co-occurrence edges) and collect the sessions
    attached to each connected node. Returns distinct session ids (minus the
    source) with the shared node name as the ``why``.
    """
    out: list[HandoffRelatedSession] = []
    seen_ids: set[str] = {session_id}
    try:
        from contexthub.graph.store import get_graph_store

        store = get_graph_store()
        uid = getattr(caller, "user_id", None)
        team = getattr(caller, "team", None)
        sub = store.session_subgraph(session_id, caller_user_id=uid, caller_team=team)
        for node in sub.get("nodes", []):
            neigh = store.neighbors(node["id"], depth=1, caller_user_id=uid, caller_team=team)
            for n2 in neigh.get("nodes", []):
                for rid in store.sessions_for_node(n2["id"]):
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    title = None
                    row = resolve_catalog(rid, caller)
                    if row:
                        title = row.get("title")
                    out.append(
                        HandoffRelatedSession(
                            id=rid, title=title, why=f"shares '{n2.get('name')}'"
                        )
                    )
                    if len(out) >= cap:
                        return out
    except Exception:
        return out
    return out


# ---------------------------------------------------------------------------
# Open threads + resume hint (heuristic v1)
# ---------------------------------------------------------------------------

_THREAD_MARKERS = ("todo", "fixme", "next step", "still need", "follow up", "follow-up")


def open_threads(messages: list[Message], cap: int = 5) -> list[str]:
    out: list[str] = []
    for m in messages:
        for raw_line in (m.text or "").splitlines():
            line = raw_line.strip()
            low = line.lower()
            if any(mark in low for mark in _THREAD_MARKERS):
                clean = _redact(line)[:200]
                if clean and clean not in out:
                    out.append(clean)
                    if len(out) >= cap:
                        return out
    return out


def resume_hint(manifest: SessionManifest, messages: list[Message], threads: list[str]) -> str:
    last_text = ""
    for m in reversed(messages):
        if m.text and m.text.strip():
            last_text = m.text.strip().splitlines()[0][:140]
            break
    project = manifest.project or "the project"
    nudge = threads[0] if threads else "Pick up from the most recent turn."
    hint = (
        f"Continue '{manifest.title}' in {project}. "
        f"Last activity: {last_text}. {nudge}"
    )
    return _redact(hint)


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------

def encode_cursor(index: int) -> str:
    return base64.urlsafe_b64encode(str(index).encode()).decode()


def decode_cursor(cursor: Optional[str], n_messages: int) -> int:
    """Decode an opaque cursor to a message index; raise ``invalid_cursor``."""
    if cursor is None or cursor == "":
        return 0
    try:
        idx = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        raise AICPError("invalid_cursor", f"Cursor '{cursor}' is not decodable.")
    if idx < 0 or idx > n_messages:
        raise AICPError("invalid_cursor", f"Cursor index {idx} is out of range.")
    return idx


# ---------------------------------------------------------------------------
# Message wire conversion + grep
# ---------------------------------------------------------------------------

def to_wire_messages(msgs: list[Message]) -> list[NormalizedMessage]:
    out: list[NormalizedMessage] = []
    for m in msgs:
        out.append(
            NormalizedMessage(
                id=m.id,
                role=m.role,
                text=_redact(m.text),
                thinking=_redact(m.thinking) if m.thinking else None,
                tool_name=m.tool_name,
                timestamp=m.timestamp,
                model=m.model,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Public builders (one per AICP verb)
# ---------------------------------------------------------------------------

def session_summary(session_id: str, caller=None) -> SummaryResponse:
    return compute_summary(resolve(session_id, caller))


def session_recent(
    session_id: str, caller=None, n: int = 20, before_cursor: Optional[str] = None
) -> RecentResponse:
    r = resolve(session_id, caller)
    total = len(r.messages)
    end = decode_cursor(before_cursor, total) if before_cursor else total
    end = max(0, min(end, total))
    start = max(0, end - max(0, n))
    window = r.messages[start:end]
    cursor = encode_cursor(start) if start > 0 else None
    return RecentResponse(messages=to_wire_messages(window), cursor=cursor)


def session_grep(session_id: str, caller=None, q: str = "", limit: int = 20) -> GrepResponse:
    r = resolve(session_id, caller)
    matches: list[GrepMatch] = []
    needle = (q or "").lower()
    if not needle:
        return GrepResponse(matches=[])
    for m in r.messages:
        text = m.text or ""
        low = text.lower()
        offset = low.find(needle)
        if offset >= 0:
            lo = max(0, offset - 60)
            hi = min(len(text), offset + len(needle) + 60)
            matches.append(
                GrepMatch(
                    message_id=m.id,
                    offset=offset,
                    role=m.role,
                    snippet=_redact(text[lo:hi]),
                )
            )
            if len(matches) >= max(1, limit):
                break
    return GrepResponse(matches=matches)


def build_handoff_packet(
    session_id: str,
    caller=None,
    levels: Optional[list[str]] = None,
    n: int = 20,
) -> HandoffPacket:
    """Assemble the full AICP HandoffPacket (envelope + Freshet extensions).

    Best-effort: graph/catalog enrichment never raises; only ``not_found``
    (no disk + no catalog) propagates. Every outbound text field is redacted
    and ``redacted=True`` is set.
    """
    if levels is None:
        levels = ["summary", "recent"]
    r = resolve(session_id, caller)
    manifest = to_manifest(r)

    summary = ""
    if "summary" in levels:
        summary = compute_summary(r).summary  # already redacted

    recent: list[NormalizedMessage] = []
    if "recent" in levels:
        # D1: n<=0 means "no recent" (never the full transcript) — matches
        # session.recent semantics and avoids leaking the whole session.
        window = r.messages[-n:] if n > 0 else []
        recent = to_wire_messages(window)

    ws, decisions = working_set_and_decisions(session_id, caller)
    # D2 (§8): redact metadata text fields too, not just message bodies.
    touched = [_redact(f) for f in extract_touched_files(r.path, r.kind)]
    related = related_sessions(session_id, caller)
    threads = open_threads(r.messages)
    hint = resume_hint(manifest, r.messages, threads)

    return HandoffPacket(
        protocol="aicp/0.1",
        session=manifest,
        summary=summary,
        recent=recent,
        more=HandoffMore(),
        issued_at=datetime.now(timezone.utc).isoformat(),
        issued_by=ISSUED_BY,
        redacted=True,
        decisions=decisions,
        touched_files=touched,
        working_set=ws,
        related_sessions=related,
        open_threads=threads,
        resume_hint=hint,
    )


def iter_stream_messages(session_id: str, caller, from_cursor: Optional[str] = None):
    """Yield SSE ``data:`` lines for the stream stub (resumable from a cursor)."""
    r = resolve(session_id, caller)
    total = len(r.messages)
    start = decode_cursor(from_cursor, total) if from_cursor else 0
    wire = to_wire_messages(r.messages)
    for i in range(start, total):
        payload = {
            "message": wire[i].model_dump(by_alias=True),
            "cursor": encode_cursor(i + 1),
        }
        yield f"data: {json.dumps(payload)}\n\n"
    yield 'data: {"done": true}\n\n'
