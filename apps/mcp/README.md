# Freshet MCP server (`freshet_mcp`)

The **Freshet AICP broker**: a local **stdio MCP server** that both Claude Code and
Codex connect to so one agent can pick up a live session from the other — mid-task,
with full context. It is a thin `httpx` proxy over the Freshet hub's REST binding
(the spec's *REST-backed broker* shape, **not** the Rust desktop sidecar of spec §10).
The MCP binding is primary; the hub REST routes mirror it 1:1. All assembly logic
lives in the hub (`contexthub.handoff`); this package only proxies and shapes JSON.

Conforms to AICP §6 (`docs/superpowers/specs/2026-06-10-aicp-session-exchange-protocol-design.md`).

## The seven tools (AICP §6 verbs — dot wire form rendered to underscores)

| Tool (MCP) | AICP verb | Hub route | Returns |
|---|---|---|---|
| `session_list(tool?, project?, limit?)` | `session.list` (L0) | `GET /v1/sessions` | `{ sessions: SessionManifest[] }` |
| `session_search(query, limit?)` | `session.search` (L0/L3) | `POST /v1/query` | `{ hits: SearchHit[], answer }` |
| `session_summary(session_id)` | `session.summary` (L1) | `GET /v1/session/{id}/summary` | `SummaryResponse` |
| `session_recent(session_id, n?)` | `session.recent` (L2) | `GET /v1/session/{id}/recent` | `RecentResponse` |
| `session_grep(session_id, query, limit?)` | `session.grep` (L3) | `GET /v1/session/{id}/grep` | `GrepResponse` |
| `session_handoff(session_id, levels?, n?)` | `session.handoff` (push) | `GET /v1/session/{id}/handoff` | **`HandoffPacket`** (verbatim) |
| `session_stream(session_id, from_cursor?)` | `session.stream` (L4) | `GET /v1/session/{id}/stream` (SSE) | **stub** → route pointer |

`session_handoff` returns the hub's `HandoffPacket` **unchanged**, so consumers get the
exact AICP envelope `{ protocol:"aicp/0.1", session, summary, recent, more:{grep,stream},
issuedAt, issuedBy, redacted }` plus Freshet's additive keys `{ decisions, touchedFiles,
workingSet, relatedSessions, openThreads, resumeHint }`. All payloads are camelCase.

`session_stream` is a **documented fast-follow stub**: deep client-side resumable
streaming over MCP is not yet wired; the tool returns a pointer to the working hub SSE
route. Mid-flight reconnect dedupe / backpressure are the fast-follow.

## Errors

Hub errors surface as readable tool errors (FastMCP `isError=True`) using the stable
AICP error set: `not_found, forbidden, consent_required, invalid_cursor, rate_limited,
internal`, formatted as `"<code>: <message>"`. The hub's structured
`{ "error", "message", "hint" }` detail is preferred; otherwise the HTTP status maps to
a code.

## Configuration (env)

| Var | Default | Purpose |
|---|---|---|
| `FRESHET_HUB_URL` | `http://localhost:8787` | hub base URL |
| `FRESHET_API_KEY` | `dev-key` | Bearer auth to the hub |
| `FRESHET_AGENT` | `mcp` | sent as `X-AICP-Agent` (consent + redaction); set to `claude` / `codex` |

## Install & run

```bash
# Use the repo's existing API venv (or any 3.10+ venv).
apps/api/.venv/bin/pip install -e apps/mcp

# Sanity import + run the stdio server.
apps/api/.venv/bin/python -c "import freshet_mcp.server"
apps/api/.venv/bin/python -m freshet_mcp        # speaks MCP JSON-RPC over stdio
```

The server speaks JSON-RPC on stdout — **never print to stdout** from tool code.

## Wiring into agents

**Claude Code** — the repo ships a project-scoped `.mcp.json` at the root:

```bash
# or register globally:
claude mcp add freshet -- python -m freshet_mcp
```

**Codex** — add to `~/.codex/config.toml` (Codex does NOT inherit the parent env, so
vars MUST live under `[mcp_servers.freshet.env]`; requires Codex CLI ≥ 0.121):

```toml
[mcp_servers.freshet]
command = "python"
args = ["-m", "freshet_mcp"]
cwd = "/path/to/context-hub/apps/mcp"
startup_timeout_sec = 15
tool_timeout_sec = 60

[mcp_servers.freshet.env]
FRESHET_HUB_URL = "http://localhost:8787"
FRESHET_API_KEY = "dev-key"
FRESHET_AGENT = "codex"
```

Point `command` at a venv's absolute `python` if `freshet_mcp` is not on the global path.
See `docs/HANDOFF.md` for the full integration guide and the offline file fallback.

## Tests

```bash
apps/api/.venv/bin/python -m pytest apps/mcp/tests -q
```

Tests monkeypatch the hub (no network): `test_server.py` asserts tool wiring + AICP wire
shapes + error mapping + that exactly the seven verbs are registered; `test_client.py`
asserts the `httpx` error mapping via `MockTransport`.
