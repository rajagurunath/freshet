# Live Handoff — Claude Code ⇄ Codex (AICP)

Freshet lets one coding agent **pick up where another left off, mid-task, with full
context** — without replaying a 33 MB transcript. You are deep in a Claude Code session on
`llm-serving-api` and want to continue in **Codex** (or Codex is stuck and wants Claude's
context). The target agent orients instantly: what was decided, which files are in play, the
last few turns, the connected entities, and where to pick up.

This is implemented as the **AI Context Protocol (AICP)** session-exchange surface. The
**Freshet MCP server** (`apps/mcp`, package `freshet_mcp`) is the broker; both Claude Code and
Codex connect to it over **stdio**. It is a thin proxy over the hub's REST binding
(`apps/api`, runs at `http://localhost:8787`), which does all the assembly. MCP is the primary
binding; REST mirrors it 1:1.

> **Realization shape.** The broker is a **Python stdio MCP server backed 1:1 by the hub's
> REST binding** — the spec's *REST-backed-broker* shape, **not** the Rust desktop sidecar of
> spec §10. MCP binding is primary; REST mirrors it.

---

## The scenario, end to end

1. You have a Claude Code (or Codex) session on disk and/or pushed to the hub.
2. The receiving agent has the `freshet` MCP server configured (see below).
3. Mid-task, the receiving agent calls the progressive-disclosure tools:
   `session_list` / `session_search` to **find** a session →
   `session_summary` / `session_recent` for **light context** →
   **`session_handoff("<id>")`** for the **full bundle** (the `HandoffPacket`).
4. The agent reads the packet — summary, decisions, touched files, working set, recent turns,
   related sessions, resume hint — and continues the task.

It works **both directions** (Claude→Codex and Codex→Claude) and is **local-first** — no
control plane. The source session **need not be pushed**: the hub resolves it from the on-disk
transcript by id (live/unpushed) first, then falls back to the hub catalog.

The Freshet desktop app exposes this as a **"Hand off"** button on any session detail page: it
calls `GET /v1/session/{id}/handoff`, renders the brief, and gives you a copy-able resume
command (`freshet.session_handoff("<id>")`) to paste into the other agent.

---

## The seven verbs (AICP §6)

Progressive disclosure across levels L0–L4 plus the push verb. Wire names use a dot; MCP tool
names render the dot as an underscore (Claude/Codex auto-namespace them to `mcp__freshet__*`).

| Verb (wire) | MCP tool | REST route | Level | Status |
|---|---|---|---|---|
| `session.list` | `session_list` | `GET /v1/sessions` *(reused)* | L0 | ✅ |
| `session.search` | `session_search` | `POST /v1/query` *(reused)* | L0/L3 | ✅ |
| `session.summary` | `session_summary` | `GET /v1/session/{id}/summary` | L1 | ✅ |
| `session.recent` | `session_recent` | `GET /v1/session/{id}/recent?n=20` | L2 | ✅ |
| `session.grep` | `session_grep` | `GET /v1/session/{id}/grep?q=&limit=` | L3 | ✅ |
| `session.stream` | `session_stream` | `GET /v1/session/{id}/stream?from_cursor=` | L4 | ⏳ stub (SSE works; deep resumability is fast-follow) |
| `session.handoff` | `session_handoff` | `GET /v1/session/{id}/handoff?levels=summary,recent&n=20` | push | ✅ |

No other tool names exist. All payloads are **camelCase on the wire** — the MCP server passes
hub JSON through verbatim, so the bytes that reach an agent are already camelCase.

---

## The HandoffPacket envelope

`session.handoff` returns the AICP `HandoffPacket`. The **envelope keys are exact**; Freshet
adds a **superset** of extra keys (never replacing the envelope):

```jsonc
{
  // ── AICP envelope (spec §6, exact) ──
  "protocol": "aicp/0.1",
  "session": {                       // SessionManifest (L0)
    "id", "tool", "title", "project",
    "startedAt", "endedAt", "messageCount",
    "tokens": { "input", "output" },
    "hasSummary", "visibility", "source"   // source: "local" (live/disk) | "hub"
  },
  "summary": "…",                    // the gist
  "recent": [ { "id", "role", "text", "thinking", "toolName", "timestamp", "model" } ],
  "more": { "grep": "session.grep", "stream": "session.stream" },
  "issuedAt": "2026-06-30T…Z",
  "issuedBy": "freshet-hub",
  "redacted": true,

  // ── Freshet extension keys (ADDITIVE superset) ──
  "decisions":       [ { "decision", "why" } ],
  "touchedFiles":    [ "…file paths from tool calls…" ],
  "workingSet":      { "repos": [], "services": [], "libraries": [] },
  "relatedSessions": [ { "id", "title", "why" } ],   // graph-linked — Freshet's unique value
  "openThreads":     [ "…unfinished work…" ],
  "resumeHint":      "Continue by …"
}
```

`SessionManifest` (L0) is the 11-field lightweight manifest: `id, tool, title, project,
startedAt, endedAt, messageCount, tokens, hasSummary, visibility, source`.

---

## Add the MCP server to Claude Code

This repo ships a project-scoped [`.mcp.json`](../.mcp.json) at the root:

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

Project-scoped servers require a one-time trust approval in `claude`. The `${VAR:-default}`
syntax means missing env vars don't fail the parse. Make sure `freshet_mcp` is importable —
install it first: `pip install -e apps/mcp` (or point `command` at the venv's absolute
`python`).

One-liner alternative (no committed file):

```bash
claude mcp add freshet -- python -m freshet_mcp
# or with explicit env:
claude mcp add freshet \
  -e FRESHET_HUB_URL=http://localhost:8787 \
  -e FRESHET_API_KEY=dev-key \
  -e FRESHET_AGENT=claude \
  -- python -m freshet_mcp
```

---

## Add the MCP server to Codex

Codex reads `~/.codex/config.toml`. **Codex does NOT inherit the parent environment** — the
vars MUST be set under `[mcp_servers.freshet.env]`. Requires **Codex CLI ≥ 0.121** for
namespaced tool registration.

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

For a venv interpreter, point `command` at its absolute `python`
(e.g. `/Users/…/context-hub/.venv/bin/python`). One-liner alternative:

```bash
codex mcp add freshet -- python -m freshet_mcp
```

### Codex setup (quick start)

```bash
# 1. Start the hub
cd /Users/gurunathlunkupalivenugopal/oss/context-hub/apps/api
uvicorn contexthub.main:create_app --factory --port 8787

# 2. (optional) backfill the graph so workingSet / relatedSessions populate
curl -s -X POST http://localhost:8787/v1/graph/build-all -H "Authorization: Bearer dev-key"

# 3. Install the broker so `python -m freshet_mcp` resolves
pip install -e /Users/gurunathlunkupalivenugopal/oss/context-hub/apps/mcp

# 4. Add the [mcp_servers.freshet] block above to ~/.codex/config.toml, then in Codex:
#    freshet.session_handoff("<a Claude session id>")
```

---

## Offline file fallback

When no MCP connection is available, the handoff can be delivered as a file: write a
`HANDOFF.md` (or an `AGENTS.md` block) containing the brief plus a one-line resume command into
the target's working dir. This needs **zero agent cooperation**.

**v1 is advisory context only** — the fallback never writes into the receiving working tree
beyond this single file (no merge/conflict, no edits). The structured `HandoffPacket` from the
live MCP path is always richer; prefer it when both agents can reach the hub.

---

## AICP conformance

### §6 verb surface

| Verb | Implemented | Notes |
|---|---|---|
| `session.list` | ✅ | reuses `GET /v1/sessions`; MCP adapter renders rows → `SessionManifest[]` |
| `session.search` | ✅ | reuses `POST /v1/query`; adapter renders citations → hits + RAG `answer` |
| `session.summary` | ✅ | prefers the richer hub-catalog summary, else first-message gist |
| `session.recent` | ✅ | last-N `NormalizedMessage` + opaque base64 cursor |
| `session.grep` | ✅ | keyword scan (semantic grep is fast-follow) |
| `session.handoff` | ✅ | the full `HandoffPacket` |
| `session.stream` | ⏳ | working SSE stub; deep resumability (reconnect dedupe, backpressure) is a documented fast-follow. MCP `session_stream` returns a pointer to the SSE route. |

### HandoffPacket envelope

Exact envelope fields — `protocol:"aicp/0.1"`, `session`, `summary`, `recent`,
`more:{grep,stream}`, `issuedAt`, `issuedBy`, `redacted` — present and conformant. Freshet's
extra keys (`decisions`, `touchedFiles`, `workingSet`, `relatedSessions`, `openThreads`,
`resumeHint`) are **additive**: a superset, never a replacement.

### Consent + redaction (§8)

- **Redaction before any bytes leave.** Every outbound text field is passed through the hub's
  `redact_text` and `redacted=true` is set on the packet. Nothing leaves un-redacted.
- **Consent path exists.** Env `FRESHET_CONSENT=allow` (the v1 dev default) bypasses. Otherwise
  a per-agent grant gate returns `consent_required` until the agent id (from the `X-AICP-Agent`
  header, set by the MCP server from `FRESHET_AGENT`) is added to `FRESHET_CONSENT_GRANTS`.
  The richer desktop one-time-grant UI is a documented fast-follow; the code path + error exist
  and are tested now.

### Stable error set

`not_found` (404), `forbidden` (403), `consent_required` (403), `invalid_cursor` (400),
`rate_limited` (429), `internal` (500). REST returns
`{"error": <code>, "message": …, "hint"?: …}`; the MCP server maps these to
`AICPToolError("<code>: <message>")` (surfaced to the agent as `isError=true`).

### Documented fast-follows (stubbed, not dropped)

- ⏳ `session.stream` deep resumability (mid-flight reconnect dedupe, backpressure).
- ⏳ MCP **resources** (`contexthub://session/{id}/summary|recent|full`) — tools only in v1.
- ⏳ Cloud broker + capability tokens + cross-user sharing (Streamable-HTTP multi-machine).
- ⏳ Desktop one-time agent-grant UI (env-based consent now).
- ⏳ `touchedFiles` for catalog-only sessions + Bash / `local_shell_call` path mining
  (disk `tool_use` extraction now).
- ⏳ Semantic grep/search (keyword + existing RAG now).
