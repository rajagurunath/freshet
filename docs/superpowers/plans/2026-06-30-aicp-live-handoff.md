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
