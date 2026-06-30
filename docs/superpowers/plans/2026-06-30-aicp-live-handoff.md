# AICP Live Handoff — Claude ⇄ Codex (architecture brief)

**Branch:** `feat/aicp-live-handoff` · **Date:** 2026-06-30
**Spec:** [AICP session-exchange protocol](../specs/2026-06-10-aicp-session-exchange-protocol-design.md)
**Status:** DIRECTION SET (this brief is the spine; a workflow fills exact externals + builds)

## The problem
Today Codex↔Claude is sorted at the **memory layer** (both tools' sessions are parsed,
normalized, graphed, searched in the shared Freshet hub) but **not the live-handoff layer**:
one agent can't pick up where the other left off, mid-task, with full context. Build that.

## What "live handoff" means (the scenario)
You're deep in a Claude Code session on `llm-serving-api`. You want to continue in **Codex**
(or Codex is stuck and wants Claude's context). The target agent must orient instantly:
what was decided, which files are in play, the last few turns, the connected entities, and
where to pick up — **without** replaying a 33 MB transcript.

## Lesson from omnigent (research-backed)
Omnigent's cross-agent handoff = **normalized-transcript replay over a control plane**, and
it's **lossy** (drops tool-call provenance; degrades to plain-text prefix for SDK harnesses).
Freshet's edge: a **richer, progressive handoff payload** (decisions + touched files + graph
context + related sessions), delivered so the target **pulls** it live — and **vendor-neutral**
(works both directions), **local-first**, no control plane.

## Core design — three decisions

### 1. The AICP handoff brief (the payload) — progressive disclosure, not a transcript dump
A structured object assembled from the normalized session + graph:
```
{
  session_id, source_tool, title, project, cwd,
  summary,                       // the gist
  decisions: [{decision, why}],  // so the target doesn't relitigate
  touched_files: [...],          // working surface (parsed from tool calls)
  working_set: {repos, services, libraries},  // from the knowledge graph
  recent_turns: [{role, text}],  // last N exchanges (immediate context)
  related_sessions: [{id, title, why}],        // graph-linked (Freshet's unique value)
  open_threads: [...],           // unfinished work
  resume_hint: "Continue by …",
}
```
Assembled by reusing existing pieces: `graph/build.py` (parse a live disk session),
`graph/store.py` (entities + related sessions), the session summary, tool-call parsing for files.

### 2. Two delivery mechanisms (complementary)
- **MCP server (primary, LIVE pull).** A **Freshet MCP server** both Claude Code and Codex
  connect to. Progressive-disclosure tools:
  `list_sessions(tool?,project?)` → `search(query)` → `handoff(session_id, depth)` →
  `session_turns(id,last_n)` → `related(id)`. Mid-task, the target calls `freshet.handoff("…")`.
  **stdio transport** for local single-user (both tools support it); Streamable HTTP is a later
  iteration for multi-machine "share a live session by URL."
- **Handoff file (explicit / offline fallback).** Write `HANDOFF.md` (or an AGENTS.md block) into
  the target's working dir + a one-line resume command. Zero agent cooperation needed.
  v1 is **advisory context only** — never writes into the receiving working tree beyond this file.

### 3. Where the logic lives — hub does the work, MCP server is a thin adapter
- **Hub (`apps/api`)** owns the assembly: `contexthub/handoff.py` + `GET /v1/handoff/{session_id}`.
  It resolves the session **from disk (live, unpushed sessions) OR the hub catalog**, then builds
  the brief. All logic is here → unit-testable in Python.
- **MCP server (`apps/mcp`, new Python pkg `freshet_mcp`)** is a thin proxy over the hub HTTP API
  (`/v1/sessions`, `/v1/query`, `/v1/handoff/{id}`, `/v1/graph/session/{id}`). Tiny, swappable.
  Rust/Streamable-HTTP sidecar deferred.

Critical detail: the **live source session may not be pushed yet** — the brief generator must read
the on-disk transcript by id (reuse `build.list_session_files` + `parse_claude`/`parse_codex`).

## MVP scope (this branch)
1. `contexthub/handoff.py` brief generator + `GET /v1/handoff/{session_id}` + models + tests.
2. `freshet_mcp` stdio MCP server (list/search/handoff/turns/related) proxying the hub + tests.
3. Integration: `.mcp.json` (Claude), Codex `config.toml` snippet, `HANDOFF.md` writer,
   desktop "Hand off" action (button → brief + resume command), docs.
4. End-to-end demo: configure Codex with the server → `freshet.handoff(<a Claude session id>)`
   returns a coherent brief → Codex continues. Plus the reverse + the file path.

## Out of scope (later)
Streamable-HTTP multi-machine sharing, real-time session streaming, writing into the receiving
working tree (merge/conflict), full bi-directional tool-call replay.

## Success criteria
- A target agent (Codex) can pull a coherent handoff brief for a **Claude** session it didn't
  create, via MCP, including decisions + files + recent turns + graph-linked sessions.
- Works both directions and via the handoff file. All existing tests stay green; new tests cover
  the brief generator + MCP tools.

---

## Build Spec (synthesized)

**Author:** lead engineer · **Date:** 2026-06-30 · **Status:** BUILD-READY
**Conforms to:** AICP §6 protocol surface (`docs/superpowers/specs/2026-06-10-aicp-session-exchange-protocol-design.md`).
**Realization shape (declared):** the broker is a **Python stdio MCP server** (`apps/mcp`, pkg `freshet_mcp`)
backed 1:1 by the hub's REST binding — this is the spec's **REST-backed-broker** shape, NOT the Rust
desktop sidecar of spec §10. MCP binding is primary; REST mirrors it 1:1. Three build agents work on
**disjoint files** (ownership map in §F).

### Global conventions (binding on all three agents)

- **Seven verbs, exact names.** Wire/dot form → MCP tool (underscore) form:
  `session.list`→`session_list`, `session.search`→`session_search`, `session.summary`→`session_summary`,
  `session.recent`→`session_recent`, `session.grep`→`session_grep`, `session.stream`→`session_stream`,
  `session.handoff`→`session_handoff`. **No other tool names.**
- **camelCase on the wire** for every AICP route/tool payload. The hub emits camelCase **directly** for the
  AICP routes (not the legacy snake-case + desktop-translate convention) because the MCP server passes hub
  JSON through verbatim to agents, so the bytes that reach an agent must already be camelCase.
- **Stable error set:** `not_found`, `forbidden`, `consent_required`, `invalid_cursor`, `rate_limited`,
  `internal`. REST returns them as `HTTPException(status, detail={"error": <code>, "message": …, "hint"?: …})`.
  HTTP status map: `not_found`→404, `forbidden`→403, `consent_required`→403, `invalid_cursor`→400,
  `rate_limited`→429, `internal`→500.
- **Redaction before any bytes leave** (spec §8): every text field in a response is passed through
  `contexthub.ingest.redact.redact_text` and `redacted=True` is set. See §D.
- **Consent path exists** (spec §8): env `FRESHET_CONSENT=allow` (v1 default) bypasses; otherwise a per-agent
  grant gate returns `consent_required`. See §D.

---

### A. AICP schemas — add to `contexthub/models.py` (Agent A)

Place after the Knowledge-graph section (~line 260), before Rules. Use a camelCase alias base so FastAPI
serializes by alias (FastAPI's `response_model_by_alias=True` default) while Python code constructs with
snake_case names.

```python
# ---------------------------------------------------------------------------
# AICP — AI Context Protocol (session exchange, §6 of the spec)
# camelCase on the wire; snake_case in Python via alias generator.
# ---------------------------------------------------------------------------
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel


class AICPModel(BaseModel):
    """Base for all AICP wire models: serialize/parse in camelCase."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class NormalizedMessage(AICPModel):
    """Wire form of Message (camelCase). One transcript message."""
    id: str
    role: Literal["user", "assistant", "system", "tool"]
    text: str
    thinking: Optional[str] = None
    tool_name: Optional[str] = None       # -> toolName
    timestamp: Optional[str] = None
    model: Optional[str] = None


class SessionManifest(AICPModel):
    """L0 manifest — lightweight, no message bodies (spec §6)."""
    id: str
    tool: str                              # claude-code | codex | kilo-code
    title: str
    project: Optional[str] = None
    started_at: Optional[str] = None       # -> startedAt
    ended_at: Optional[str] = None         # -> endedAt
    message_count: int = 0                 # -> messageCount
    tokens: Optional[TokenCounts] = None
    has_summary: bool = False              # -> hasSummary
    visibility: str = "private"            # company | team | private
    source: str = "hub"                    # local | hub


class SummaryResponse(AICPModel):
    """session.summary (L1)."""
    summary: str
    generated_by: str                      # -> generatedBy  (e.g. "hub-catalog" | "disk-first-message")
    generated_at: Optional[str] = None     # -> generatedAt


class RecentResponse(AICPModel):
    """session.recent (L2)."""
    messages: list[NormalizedMessage] = Field(default_factory=list)
    cursor: Optional[str] = None           # opaque base64 of the last message index


class GrepMatch(AICPModel):
    message_id: str                        # -> messageId
    offset: int
    role: str
    snippet: str


class GrepResponse(AICPModel):
    """session.grep (L3)."""
    matches: list[GrepMatch] = Field(default_factory=list)


class SearchHit(AICPModel):
    session_id: str                        # -> sessionId
    score: float
    snippet: str
    manifest: Optional[SessionManifest] = None


class SearchResponse(AICPModel):
    """session.search (L0/L3)."""
    hits: list[SearchHit] = Field(default_factory=list)
    answer: Optional[str] = None           # Freshet extension: the RAG answer when reusing /v1/query


# --- handoff envelope + Freshet extension keys ---

class HandoffMore(AICPModel):
    grep: str = "session.grep"
    stream: str = "session.stream"


class HandoffDecision(AICPModel):
    decision: str
    why: Optional[str] = None


class HandoffWorkingSet(AICPModel):
    repos: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)


class HandoffRelatedSession(AICPModel):
    id: str
    title: Optional[str] = None
    why: Optional[str] = None


class HandoffPacket(AICPModel):
    """session.handoff (push). Spec envelope + Freshet superset keys (never replacing the envelope)."""
    # --- AICP envelope (spec §6, exact) ---
    protocol: Literal["aicp/0.1"] = "aicp/0.1"
    session: SessionManifest
    summary: str = ""
    recent: list[NormalizedMessage] = Field(default_factory=list)
    more: HandoffMore = Field(default_factory=HandoffMore)
    issued_at: str                         # -> issuedAt   (ISO8601, now)
    issued_by: str                         # -> issuedBy   ("freshet-hub")
    redacted: bool = True
    # --- Freshet extension keys (ADDITIVE; a superset, not a replacement) ---
    decisions: list[HandoffDecision] = Field(default_factory=list)
    touched_files: list[str] = Field(default_factory=list)     # -> touchedFiles
    working_set: HandoffWorkingSet = Field(default_factory=HandoffWorkingSet)  # -> workingSet
    related_sessions: list[HandoffRelatedSession] = Field(default_factory=list)  # -> relatedSessions
    open_threads: list[str] = Field(default_factory=list)      # -> openThreads
    resume_hint: str = ""                  # -> resumeHint
```

**`TokenCounts` already exists** (lines 17-19) — reuse it. `Message` (lines 22-29) stays snake-case for
the rest of the app; `NormalizedMessage` is its camelCase wire twin used only by AICP routes.

---

### B. Assembly module — new file `contexthub/handoff.py` (Agent A)

All assembly logic lives here; routes are thin (decision #3). Reuse-map citations in brackets.

```python
from __future__ import annotations
import base64, json, os
from datetime import datetime, timezone
from typing import Optional
```

**Error type + status map** (used by routes):
```python
_AICP_STATUS = {"not_found":404,"forbidden":403,"consent_required":403,
                "invalid_cursor":400,"rate_limited":429,"internal":500}

class AICPError(Exception):
    def __init__(self, code: str, message: str, hint: Optional[str] = None):
        self.code, self.message, self.hint = code, message, hint
        super().__init__(f"{code}: {message}")
```

**Consent gate** (spec §8; reads env directly so `config.py` is NOT touched → keeps file ownership disjoint):
```python
def require_consent(agent: Optional[str]) -> None:
    mode = os.environ.get("FRESHET_CONSENT", "allow").strip().lower()
    if mode == "allow":
        return
    grants = {a.strip() for a in os.environ.get("FRESHET_CONSENT_GRANTS","").split(",") if a.strip()}
    if agent and agent in grants:
        return
    raise AICPError("consent_required",
        f"Agent {agent or '(unknown)'} is not granted access to Freshet sessions.",
        hint="Set FRESHET_CONSENT=allow (dev) or add the agent id to FRESHET_CONSENT_GRANTS.")
```

**Session resolution — disk-first (live/unpushed), then catalog** [build.py §1, vectors.py §3].
Copies the verbatim resolve-by-id pattern from `routes.build_session_graph` (lines 952-964):
```python
def resolve_disk(session_id):  # -> Optional[(NormalizedSession, str disk_summary, str path, str kind)]
    from contexthub.graph.build import list_session_files, parse_claude, parse_codex, to_session
    for path, kind in list_session_files():
        if os.path.splitext(os.path.basename(path))[0] == session_id:
            parsed = parse_claude(path) if kind == "claude" else parse_codex(path)
            if not parsed:
                return None
            sess, summary = to_session(parsed)
            return sess, summary, path, kind
    return None

def resolve_catalog(session_id, caller):  # -> Optional[dict] (LanceDB row)
    from contexthub.storage.vectors import get_vector_store
    return get_vector_store().get_session(
        session_id, caller_user_id=caller.user_id, caller_team=caller.team, enforce_visibility=True)
```
A handoff/summary/recent/grep call resolves **both**; raises `AICPError("not_found", …)` if neither
present. When catalog row is missing but disk hit exists → **live unpushed** (`source="local"`,
`visibility="private"`). When disk missing but catalog hit exists → **archived/pushed**
(`source="hub"`; messages rebuilt from the blob, see below).

**Messages source** (last-N for recent/handoff):
- disk path → `sess.messages` (list[Message]) [build.py `to_session`].
- catalog-only path → load raw blob `get_blob_store().get_session(author_id=row["author"], session_id=id)`,
  `json.loads`, build `Message` list from `raw["messages"]` [routes.get_session lines 405-413].

**`SessionManifest` builder:**
```python
def to_manifest(*, sess=None, row=None) -> SessionManifest
```
Prefer `row` fields when present (richer): `id, tool, title, project, started_at=row["created_at"],
ended_at=row.get("updated_at"), message_count=row["message_count"],
tokens=TokenCounts(input=row["tokens_input"], output=row["tokens_output"]),
has_summary=bool(row.get("summary")), visibility=row["visibility"], source="hub"`.
Disk-only: from `sess` (`tokens=TokenCounts(0,0)`, `has_summary=False`, `visibility="private"`,
`source="local"`, `started_at=parsed ts`).

**Summary** [vectors.py §3 — catalog summary is the richer LLM one]:
prefer `row["summary"]` (`generated_by="hub-catalog"`, `generated_at=row["created_at"]`); else the disk
`to_session` summary (`generated_by="disk-first-message"`). Both routed through `redact_text`.

**Touched files — NEW parsing pass** [research §6; no existing extractor]. Model after the line-by-line
`json.loads` loops in `parse_claude`/`parse_codex`:
```python
def extract_touched_files(path: str, kind: str) -> list[str]
```
- **Claude** (`~/.claude/projects/**/*.jsonl`): for each line with `o["type"]=="assistant"`, iterate
  `o["message"]["content"]` (a list); for blocks where `b["type"]=="tool_use"`:
  if `b["name"] in {"Edit","Write","Read","NotebookEdit"}` → add `b["input"].get("file_path")` or
  `b["input"].get("notebook_path")`; if `b["name"]=="Bash"` → skip path-extraction in v1 (record nothing;
  shell-path mining is a fast-follow).
- **Codex** (`~/.codex/sessions/**/*.jsonl`): for each line with `o["type"]=="response_item"`,
  `payload=o["payload"]`; if `payload["type"]=="function_call"` → `json.loads(payload["arguments"])` and pull
  `.get("file_path")`/`.get("path")`; `local_shell_call` → skip in v1 (fast-follow).
- Dedupe preserving order; cap 50. Catalog-only sessions (no disk path) → `[]` in v1 (documented
  fast-follow: persist touched_files at ingest).

**Working set + decisions + related sessions — from the graph** [store.py §2]:
```python
def working_set_and_decisions(session_id, caller) -> (HandoffWorkingSet, list[HandoffDecision])
def related_sessions(session_id, caller) -> list[HandoffRelatedSession]
```
- `sub = get_graph_store().session_subgraph(id, caller_user_id=…, caller_team=…)`.
- **working_set** kind map (NER `_GRAPH_KINDS`): `repo`→`repos`, `service`→`services`, `tool`→`libraries`
  (node `name`). **decisions**: nodes with `kind=="decision"` → `HandoffDecision(decision=name, why=summary)`.
- **related_sessions** (Freshet's unique value, no embeddings): for each node in `sub["nodes"]`,
  `neighbors(node_id, depth=1, caller_user_id=…, caller_team=…)` (traverses `same_as` + co-occurrence),
  then `sessions_for_node(n2)` on each connected node; collect distinct ids minus `session_id`; `why` =
  the shared node name; `title` via `resolve_catalog(rid, caller)` (skip if not visible); cap 5.

**Open threads + resume hint** (heuristic v1, documented fast-follow for LLM-derived):
- `open_threads`: scan recent message texts for lines containing `TODO`/`FIXME`/`next step`/`still need`;
  cap 5.
- `resume_hint`: template `f"Continue '{title}' in {project}. Last activity: {gist of last turn}. "
  + (first open thread or "Pick up from the most recent turn.")`.

**Cursor** (recent/stream): `cursor = base64.urlsafe_b64encode(str(index).encode()).decode()`; decode +
`ValueError`/out-of-range → `AICPError("invalid_cursor", …)`.

**Grep**: keyword scan over message texts; for each case-insensitive hit emit
`GrepMatch(message_id=m.id, offset=<char index>, role=m.role, snippet=<±60 chars>)`; honor `limit`.

**Top-level builder:**
```python
def build_handoff_packet(session_id, caller, levels: list[str], n: int) -> HandoffPacket
```
1. `require_consent(agent)` is done in the route (it has the header); builder assumes consent ok.
2. resolve disk+catalog; not_found if neither.
3. `session = to_manifest(...)`; `summary = compute_summary(...)` (only if `"summary" in levels`);
   `recent = last-N NormalizedMessage` (only if `"recent" in levels`, default n=20).
4. `decisions, working_set = working_set_and_decisions(...)`;
   `touched_files = extract_touched_files(path,kind)` when disk path known else `[]`;
   `related = related_sessions(...)`; `open_threads`, `resume_hint`.
5. **redact** every text field (summary, each message.text/thinking, decision.decision/why,
   open_threads, resume_hint) via `redact_text`; set `redacted=True`.
6. `issued_at=datetime.now(timezone.utc).isoformat()`, `issued_by="freshet-hub"`,
   `more=HandoffMore()`. Return `HandoffPacket`.

**Helper for routes** to convert `Message`→`NormalizedMessage`:
```python
def to_wire_messages(msgs: list[Message]) -> list[NormalizedMessage]
```

---

### C. REST binding — append to `contexthub/api/routes.py` (Agent A)

Append **after** `get_graph_for_session` (~line 982), before the Rules banner (line 984). New tag
`["aicp"]`. Add a shared catcher that maps `AICPError`→`HTTPException`:

```python
def _aicp(fn):  # tiny wrapper used inside each route body
    ...
# Simpler: each route wraps its body in try/except AICPError -> raise HTTPException(
#   status_code=_AICP_STATUS[e.code], detail={"error": e.code, "message": e.message, **({"hint": e.hint} if e.hint else {})})
```

Each authed route: `caller: Caller = Depends(require_api_key)` and `request: Request` (to read the
`x-aicp-agent` header for consent). Heavy imports lazy inside the function.

| Verb | Route (NEW unless noted) | Response model | Notes |
|---|---|---|---|
| `session.list` | **reuse** `GET /v1/sessions` (existing `list_sessions`, returns `SessionPage`) | `SessionPage` | MCP adapter renders rows→`SessionManifest[]` (the one mapping the thin adapter does; see §E). REST stays as-is. |
| `session.search` | **reuse** `POST /v1/query` (existing `query`, returns `QueryResponse`) | `QueryResponse` | MCP adapter renders `citations`→`hits` + passes `answer`. |
| `session.summary` | `GET /v1/session/{session_id}/summary` | `SummaryResponse` | `require_consent`; resolve; redact. |
| `session.recent` | `GET /v1/session/{session_id}/recent?n=20&before_cursor=` | `RecentResponse` | last-n wire messages + cursor. |
| `session.grep` | `GET /v1/session/{session_id}/grep?q=&limit=20` | `GrepResponse` | keyword scan. |
| `session.handoff` | `GET /v1/session/{session_id}/handoff?levels=summary,recent&n=20` | `HandoffPacket` | the bundle; `levels` is a comma string parsed to list. |
| `session.stream` | `GET /v1/session/{session_id}/stream?from_cursor=` | `StreamingResponse` (SSE) | **working stub** (§G); resumability hardening = fast-follow. |

Route bodies are 3-6 lines each: read `agent = request.headers.get("x-aicp-agent")`;
`from contexthub import handoff`; `handoff.require_consent(agent)`; call the matching `handoff.*` builder;
return. Declare `response_model=…`. Note: the singular `/v1/session/{id}/...` prefix does **not** collide
with the existing plural `/v1/sessions/{id}` routes.

**SSE stub** (`session.stream`): `StreamingResponse(gen(), media_type="text/event-stream")` where `gen`
yields `data: {json of {message, cursor}}\n\n` for each message after `from_cursor`, then
`data: {"done": true}\n\n`. Decode `from_cursor` via the handoff cursor helper (`invalid_cursor` on bad
token). This is a real, working stream over the resolved messages; **deep resumability semantics
(backpressure, mid-flight reconnect dedupe) are an explicitly documented fast-follow**.

---

### D. Consent + redaction (spec §8) — exact locations

- **Redaction** lives in `handoff.py` step 5 of `build_handoff_packet` and in the
  `summary`/`recent`/`grep` builders — every outbound text field passes through
  `contexthub.ingest.redact.redact_text(text)` (returns `(clean, count)`), and `redacted=True` is set on
  `HandoffPacket`. Nothing leaves un-redacted.
- **Consent** lives in `handoff.require_consent(agent)` (above), called at the top of every AICP route
  body. `agent` = the `X-AICP-Agent` request header (the MCP server sets it from `FRESHET_AGENT`). Default
  `FRESHET_CONSENT=allow` bypasses (v1 dev). Set `FRESHET_CONSENT=prompt` (or anything ≠ allow) and an
  agent not in `FRESHET_CONSENT_GRANTS` → `AICPError("consent_required")` → HTTP 403
  `{"error":"consent_required","hint":…}`. The richer desktop one-time-grant UI of spec §8 is a
  **documented fast-follow**; the code path + error are present and tested now.

---

### E. Freshet MCP server — new package `apps/mcp/` (Agent B)

**SDK:** official `mcp` (FastMCP 1.x). Pin `mcp>=1.28,<2` (avoid the v2 alpha rename). `requires-python>=3.10`.
**Transport:** stdio. Thin httpx proxy over the hub REST binding. Plain tool names (Claude/Codex namespace
them as `mcp__freshet__*` automatically — no manual prefixing).

Layout:
```
apps/mcp/
├── pyproject.toml
├── README.md
└── freshet_mcp/
    ├── __init__.py
    ├── __main__.py        # from freshet_mcp.server import main; main()
    ├── _client.py         # httpx wrapper + AICP error mapping
    └── server.py          # FastMCP instance + 7 tools
```

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "freshet-mcp"
version = "0.1.0"
description = "Freshet AICP broker — stdio MCP server proxying the Context Hub REST binding"
requires-python = ">=3.10"
dependencies = ["mcp>=1.28,<2", "httpx>=0.27"]

[tool.setuptools.packages.find]
include = ["freshet_mcp*"]

[project.optional-dependencies]
dev = ["pytest>=8.0.0"]
```

`_client.py` — env + error mapping (errors surface as the stable AICP set):
```python
import json, os, httpx

HUB = os.environ.get("FRESHET_HUB_URL", "http://localhost:8787").rstrip("/")
KEY = os.environ.get("FRESHET_API_KEY", "dev-key")
AGENT = os.environ.get("FRESHET_AGENT", "mcp")
_STATUS_TO_CODE = {404:"not_found",403:"forbidden",400:"invalid_cursor",429:"rate_limited"}

class AICPToolError(RuntimeError):  # FastMCP turns a raised exception into isError=True
    pass

def hub_request(method, path, *, params=None, json_body=None, timeout=60.0) -> dict:
    headers = {"Authorization": f"Bearer {KEY}", "X-AICP-Agent": AGENT}
    try:
        r = httpx.request(method, f"{HUB}{path}", params=params, json=json_body,
                          headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise AICPToolError(f"internal: hub unreachable ({e})")
    if r.status_code >= 400:
        code, msg = _STATUS_TO_CODE.get(r.status_code, "internal"), r.text
        try:
            d = r.json().get("detail")
            if isinstance(d, dict):
                code, msg = d.get("error", code), d.get("message", msg)
            elif isinstance(d, str):
                msg = d
        except Exception:
            pass
        raise AICPToolError(f"{code}: {msg}")
    return r.json()
```

`server.py` — FastMCP + 7 tools (server-instructions field describes when to use them, ≤2KB, for
Claude's tool-search). Each tool maps to the matching hub route; `session_list`/`session_search` do the
trivial AICP-shape render documented in §C:
```python
from mcp.server.fastmcp import FastMCP
from freshet_mcp._client import hub_request

mcp = FastMCP("freshet", instructions=(
    "Freshet AICP broker: pull live cross-agent handoff context for Claude Code / Codex sessions. "
    "Progressive disclosure — session_list/session_search to find a session, session_summary/"
    "session_recent for context, session_handoff for the full bundle (decisions, touched files, "
    "working set, recent turns, related sessions, resume hint). session_grep to find-in-session; "
    "session_stream to replay the transcript."))

@mcp.tool()
def session_list(tool: str | None = None, project: str | None = None, limit: int = 50) -> dict:
    """List sessions as AICP SessionManifest[] (L0)."""
    params = {k: v for k, v in {"tool": tool, "project": project, "limit": limit}.items() if v is not None}
    page = hub_request("GET", "/v1/sessions", params=params)
    rows = page.get("items", page if isinstance(page, list) else [])
    def manifest(r):
        return {"id": r["id"], "tool": r["tool"], "title": r["title"], "project": r.get("project"),
                "startedAt": r.get("createdAt") or r.get("created_at"),
                "endedAt": r.get("updatedAt") or r.get("updated_at"),
                "messageCount": r.get("messageCount", r.get("message_count", 0)),
                "tokens": {"input": r.get("tokensInput", r.get("tokens_input", 0)),
                           "output": r.get("tokensOutput", r.get("tokens_output", 0))},
                "hasSummary": bool(r.get("summary")), "visibility": r.get("visibility", "company"),
                "source": "hub"}
    return {"sessions": [manifest(r) for r in rows]}

@mcp.tool()
def session_search(query: str, top_k: int = 8) -> dict:
    """Search sessions (AICP session.search). Returns hits[] + the RAG answer."""
    res = hub_request("POST", "/v1/query", json_body={"question": query, "top_k": top_k})
    hits = [{"sessionId": c["session_id"], "score": c.get("score", 0.0), "snippet": c.get("snippet", ""),
             "manifest": {"id": c["session_id"], "title": c.get("title", ""), "tool": c.get("tool", "")}}
            for c in res.get("citations", [])]
    return {"hits": hits, "answer": res.get("answer")}

@mcp.tool()
def session_summary(session_id: str) -> dict:
    """Structured summary of one session (AICP session.summary, L1)."""
    return hub_request("GET", f"/v1/session/{session_id}/summary")

@mcp.tool()
def session_recent(session_id: str, n: int = 20) -> dict:
    """Last n normalized messages (AICP session.recent, L2)."""
    return hub_request("GET", f"/v1/session/{session_id}/recent", params={"n": n})

@mcp.tool()
def session_grep(session_id: str, q: str, limit: int = 20) -> dict:
    """Keyword find-in-session (AICP session.grep, L3)."""
    return hub_request("GET", f"/v1/session/{session_id}/grep", params={"q": q, "limit": limit})

@mcp.tool()
def session_handoff(session_id: str, levels: str = "summary,recent", n: int = 20) -> dict:
    """One-shot HandoffPacket: the full cross-agent handoff bundle (AICP session.handoff)."""
    return hub_request("GET", f"/v1/session/{session_id}/handoff", params={"levels": levels, "n": n})

@mcp.tool()
def session_stream(session_id: str, from_cursor: str | None = None) -> dict:
    """STUB (fast-follow): returns a pointer to the SSE stream route; full client-side
    resumable streaming is not yet wired through MCP."""
    return {"note": "session_stream is a documented fast-follow; use the REST SSE route directly.",
            "route": f"/v1/session/{session_id}/stream", "fromCursor": from_cursor}

def main() -> None:
    mcp.run(transport="stdio")  # NEVER print to stdout elsewhere — it's the JSON-RPC channel
```

`__main__.py`:
```python
from freshet_mcp.server import main
main()
```
Launch: `python -m freshet_mcp`. Tools return `dict` → FastMCP emits `structuredContent` + a JSON text
block. A raised `AICPToolError` → tool result `isError=True` carrying `"<code>: <message>"`.

---

### F. File ownership map — proven DISJOINT (3 parallel agents)

| Agent | Files (exhaustive) | Touches |
|---|---|---|
| **A · Hub** | `apps/api/contexthub/handoff.py` (new), `apps/api/contexthub/models.py` (edit: §A), `apps/api/contexthub/api/routes.py` (edit: §C, append-only after L982), `apps/api/tests/test_handoff.py` (new) | nothing under `apps/mcp`, `apps/desktop`, root |
| **B · MCP** | `apps/mcp/**` (all new: pyproject, README, `freshet_mcp/{__init__,__main__,_client,server}.py`, `apps/mcp/tests/test_tools.py`) | nothing under `apps/api`, `apps/desktop`, root |
| **C · Integration** | `apps/desktop/src/lib/api/client.ts` (edit), `apps/desktop/src/pages/SessionDetailPage.tsx` (edit), `.mcp.json` (new, root), `docs/HANDOFF.md` (new) | nothing under `apps/api`, `apps/mcp` |

No file is touched by two agents. **`config.py` is intentionally NOT modified** (consent reads
`os.environ` directly), preserving disjointness. Agent A's three edited files are append-only / additive,
no shared functions reordered. (Git already shows `routes.py`, `models.py`, `client.ts`,
`SessionDetailPage.tsx` as `M` from prior work — each remains owned by exactly one agent here.)

---

### G. Desktop "Hand off" action (Agent C)

- **`client.ts`** — add one method (AICP routes are already camelCase, so do **not** run `camelify`):
```ts
/** Pull the AICP HandoffPacket for a session (already camelCase on the wire). */
async getHandoff(sessionId: string, opts?: { levels?: string; n?: number }):
    Promise<HandoffPacket> {
  const qs = this.buildQuery({ levels: opts?.levels ?? "summary,recent", n: opts?.n ?? 20 });
  return this.request<HandoffPacket>("GET", `/v1/session/${sessionId}/handoff${qs}`);
}
```
  Add a `HandoffPacket` TS interface (mirror §A camelCase) near the other exported types.
- **`SessionDetailPage.tsx`** — add a **"Hand off"** `Button` in the detail header (next to the existing
  share/branch actions). On click: `await client.getHandoff(id)`, open a `Modal` showing
  summary, decisions, touched files, working set, related sessions, recent turns, and a copy-able
  **resume command**: ``Copy this into the other agent: `freshet.session_handoff("<id>")` `` plus
  `resumeHint`. Reuse the existing `useToast` for "Copied". No new files.

---

### H. Integration configs (Agent C)

**`.mcp.json`** (repo root, committed) — Claude Code, project scope:
```json
{
  "mcpServers": {
    "freshet": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "freshet_mcp"],
      "env": {
        "FRESHET_HUB_URL": "${FRESHET_HUB_URL:-http://localhost:8787}",
        "FRESHET_API_KEY": "${FRESHET_API_KEY:-dev-key}",
        "FRESHET_AGENT": "claude"
      }
    }
  }
}
```
(Defaults via `${VAR:-default}` so missing vars don't fail parse. Project-scoped servers require a
one-time trust approval in `claude`.)

**Codex `~/.codex/config.toml`** snippet (document in HANDOFF.md):
```toml
[mcp_servers.freshet]
command = "python"
args = ["-m", "freshet_mcp"]
cwd = "/Users/gurunathlunkupalivenugopal/oss/context-hub/apps/mcp"
startup_timeout_sec = 15
tool_timeout_sec = 60

[mcp_servers.freshet.env]
FRESHET_HUB_URL = "http://localhost:8787"
FRESHET_API_KEY = "dev-key"
FRESHET_AGENT = "codex"
```
(Codex does NOT inherit the parent env — vars MUST be set under `[mcp_servers.freshet.env]`. Requires
Codex CLI ≥ 0.121 for namespaced tool registration. For a venv interpreter, point `command` at its
absolute `python`.)

**`docs/HANDOFF.md`** must contain: (1) the verb surface table (the 7 verbs ↔ MCP tool names ↔ REST
routes); (2) both configs above + `claude mcp add` / `codex mcp add` one-liners; (3) the **file
fallback** (an offline `HANDOFF.md`/AGENTS.md block — v1 is advisory context only, never writes into the
receiving working tree); (4) an explicit **"AICP conformance"** section (§J).

---

### I. Demo + test plan

**End-to-end demo (both directions + file):**
1. Start hub: `uvicorn contexthub.main:create_app --factory --port 8787` (API_KEYS includes a triple).
2. Backfill a graph so working_set/related populate: `POST /v1/graph/build-all` (or build-session).
3. Register `freshet` in Codex (`config.toml`) → in Codex call `freshet.session_handoff("<a Claude
   session id>")` → returns a coherent `HandoffPacket` (envelope + decisions/files/recent/related) →
   Codex continues the task. Reverse: register in Claude (`.mcp.json`), hand off a Codex session.
4. **Live unpushed path**: pick a Claude session id that is on disk but NOT pushed to the catalog →
   `session_handoff` still resolves it from disk (`source:"local"`).
5. Desktop: open a session → "Hand off" → modal shows the brief + copy resume command.

**`apps/api/tests/test_handoff.py` (Agent A)** — self-contained, mirrors `tests/test_graph.py`
(`_clear_caches()`, module-scoped `tmp_dirs` + `client` fixtures, `ALICE`/`BOB` headers, seed catalog via
`POST /v1/sessions`, seed graph via `get_graph_store().upsert_node/upsert_edge`). Cases:
- `summary`/`recent`/`grep`/`handoff` happy paths over a **seeded catalog** session.
- **Live-unpushed**: monkeypatch `contexthub.graph.build.list_session_files` (or `.HOME`) to a tmp dir with
  a crafted `.jsonl` whose stem == the session id; assert `handoff` resolves from disk, `source=="local"`.
- **HandoffPacket conformance**: assert exact envelope keys `{protocol=="aicp/0.1", session, summary,
  recent, more=={grep,stream}, issuedAt, issuedBy, redacted==True}` AND the extension keys
  `{decisions, touchedFiles, workingSet, relatedSessions, openThreads, resumeHint}` are present;
  assert **camelCase** on the wire (e.g. `messageCount`, `issuedAt`, `touchedFiles`).
- **SessionManifest** shape: all 11 keys present.
- **touched_files**: craft a Claude `.jsonl` with a `tool_use`/`Edit` block → assert the `file_path`
  appears in `touchedFiles`.
- **working_set/related/decisions**: seed graph (`repo`,`service`,`tool`,`decision` nodes + a second
  session sharing a node) → assert mapping + a related session id.
- **Redaction**: seed a message containing a secret (e.g. `sk-…`) → assert it is `[REDACTED:…]` in the
  packet and `redacted==True`.
- **Consent**: set `FRESHET_CONSENT=prompt` with no grant + `X-AICP-Agent: codex` → expect 403
  `{"error":"consent_required"}`; add `FRESHET_CONSENT_GRANTS=codex` → succeeds.
- **Errors**: unknown id → 404 `{"error":"not_found"}`; bad `from_cursor` on stream → 400
  `{"error":"invalid_cursor"}`.
- **stream stub**: GET `/v1/session/{id}/stream` yields ≥1 `data:` event and a terminating
  `{"done": true}`.

**`apps/mcp/tests/test_tools.py` (Agent B)** — monkeypatch `freshet_mcp._client.hub_request` to a fake
returning canned hub JSON; assert each tool returns the AICP wire shape and that a hub 404 →
`AICPToolError("not_found: …")`. (No live hub needed.)

---

### J. AICP conformance checklist

**Conforms now:**
- ✅ All **7 verbs** present, exact names (dot wire / underscore MCP): `session_list, session_search,
  session_summary, session_recent, session_grep, session_stream, session_handoff`.
- ✅ **HandoffPacket** envelope exact: `protocol:"aicp/0.1", session, summary, recent, more:{grep,stream},
  issuedAt, issuedBy, redacted`; Freshet extension keys are **additive** (`decisions, touchedFiles,
  workingSet, relatedSessions, openThreads, resumeHint`).
- ✅ **SessionManifest** (L0): `id, tool, title, project, startedAt, endedAt, messageCount, tokens,
  hasSummary, visibility, source`.
- ✅ **camelCase** on the wire (alias-generator base model).
- ✅ **Stable error set** (`not_found, forbidden, consent_required, invalid_cursor, rate_limited,
  internal`) at REST and surfaced through MCP.
- ✅ **Redaction before any bytes leave** (reuses `redact_text`); `redacted=True`.
- ✅ **Consent path**: `consent_required` returned pre-grant; `FRESHET_CONSENT=allow` dev bypass.
- ✅ **MCP-primary + REST mirror 1:1**; **REST-backed Python stdio broker** realization declared.
- ✅ **Live unpushed session** resolved by id from disk (build.py) then catalog.

**Documented fast-follows (stubbed, not dropped):**
- ⏳ `session.stream` deep **resumability** (mid-flight reconnect dedupe, backpressure) — SSE stub works
  now; MCP `session_stream` returns a route pointer.
- ⏳ **MCP resources** (`contexthub://session/{id}/summary|recent|full`) — tools only in v1.
- ⏳ **Cloud broker + capability tokens + cross-user sharing** — v2 (spec §8 cross-user).
- ⏳ Desktop **one-time agent-grant UI** — env-based consent now; modal later.
- ⏳ **touched_files for catalog-only sessions** + Bash/`local_shell_call` path mining — disk tool_use
  extraction now.
- ⏳ **Semantic** grep/search — keyword + existing RAG now.

