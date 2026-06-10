# AICP — AI Context Protocol (Session Exchange) — Design

**Status:** Draft for implementation
**Date:** 2026-06-10
**Owner:** Context Hub
**Related:** [ARCHITECTURE.md](../../../ARCHITECTURE.md), the pluggable LLM layer (`apps/api/contexthub/llm.py`), local parsers (`apps/desktop/src/lib/parsers/`).

---

## 1. Summary

AICP (AI Context Protocol) is a small, vendor-neutral protocol for **progressive-disclosure session exchange** between AI coding agents (Claude Code, Codex, Kilo Code, …) and context brokers.

It lets one agent pick up the context of work done in another — e.g. Codex continuing an auth refactor started in Claude Code — and lets developers share sessions through the company hub. The consumer pulls only as much as it needs, in tiers:

1. **Manifest** (what sessions exist)
2. **Summary** (the structured gist)
3. **Recent** (the live working context)
4. **Find-in-session** (keyword/semantic matches)
5. **Full stream** (the complete transcript, resumable)

Plus a one-shot **handoff** that bundles summary + recent for the common "hand this to another agent" case.

### Why a new protocol
Research (June 2026) confirms the gap. MCP standardizes agent→tool/resource access; A2A standardizes agent→agent task delegation; ACP (IBM) and AGNTCY standardize messaging and discovery. **None standardizes exchanging captured *session history* with summary→recent→search→full tiering.** The closest artifacts are `SKILL.md`/`AGENTS.md` (portable *instructions*, not history) and one proprietary CLI. AICP fills that gap and rides on existing rails rather than reinventing transport.

References: Survey of agent interoperability protocols (arXiv:2505.02279); MCP (Anthropic); A2A (Google); ACP (IBM/Linux Foundation); AGNTCY/OASF (Cisco).

---

## 2. Goals / Non-goals

### Goals
- One transport-agnostic protocol where **"hub" and "another agent" are just peers** implementing the same interface.
- Zero-friction adoption by *today's* coding agents: they are already MCP clients.
- Progressive disclosure so a consumer controls its own context budget.
- Privacy-first: visibility scopes + redaction always; explicit consent before any read.
- Cross-tool normalization: a consumer reads any tool's session in one schema.

### Non-goals (v1)
- Agent **task delegation** (that's A2A's job; AICP moves context, not work).
- Real-time collaborative editing of a live session.
- A new embedding/RAG engine (reuse the hub's).
- Cloud broker, capability tokens, cross-user sharing — **designed here, built in v2.**

---

## 3. Terminology

- **Broker** — a server that exposes sessions over AICP. Two kinds:
  - **Local broker** — the Context Hub desktop app, exposing the user's local sessions across all tools.
  - **Cloud broker** — the Context Hub central API, exposing the company-wide corpus.
- **Consumer** — an AICP client (a coding agent) that requests sessions.
- **Provider** — the broker that owns the data being requested (always the server side of an exchange).
- **NormalizedSession / NormalizedMessage** — the existing shared schema (`apps/desktop/src/lib/types.ts`, `apps/api/contexthub/models.py`).

---

## 4. Architecture

```
   Claude Code ─┐                         ┌─ session.list / search   (L0)
   Codex      ──┼── MCP client ──▶ BROKER ┤  session.summary         (L1)
   Kilo Code  ─┘                  (server)│  session.recent          (L2)
                                          │  session.grep            (L3)
                                          ├─ session.stream          (L4)
                                          └─ session.handoff (push bundle)

 LOCAL broker  = Context Hub desktop app  → local sessions, ALL tools
 CLOUD broker  = Context Hub central API  → company-wide corpus
```

- Agents are always **clients**. They never host a server and never talk to each other directly.
- "Codex reads Claude Code's session" = Codex asks the **local broker** (the desktop app), which already parsed Claude Code's `.jsonl` into the normalized schema.
- Both brokers speak the identical AICP surface; only their corpus and auth differ.

**Rejected alternative:** each agent hosts its own MCP server. More moving parts, and it loses the desktop app's cross-tool normalization (the thing that makes Codex↔Claude exchange work at all).

---

## 5. Transport & bindings

**MCP-primary + a thin HTTP/SSE REST binding.**

- **MCP binding (primary).** Each verb below is an MCP **tool**; the addressable ones are also MCP **resources** (e.g. `contexthub://session/{id}/summary`, `/recent`, `/full`) so agents can attach them as context. Local broker uses **stdio** (same machine) or local **HTTP/SSE** (LAN). Coding agents add one line to their MCP config — no new client code.
- **REST binding (secondary).** The same verbs as `GET/POST` routes with **SSE** for streaming, for the cloud broker and any non-MCP caller. Mirrors the MCP surface 1:1.

The wire schema (below) is binding-independent; bindings only differ in framing.

---

## 6. Protocol surface

All payloads are JSON. Field names are camelCase on the wire (matching the desktop type contract); the REST binding follows the existing client's case-translation convention.

| Verb | Level | Request | Response |
|---|---|---|---|
| `session.list` | L0 | `{filters?, cursor?, limit?}` | `{sessions: SessionManifest[], nextCursor?}` |
| `session.search` | L0/L3 | `{query, filters?, topK?}` | `{hits: [{sessionId, score, snippet, manifest}]}` |
| `session.summary` | L1 | `{id}` | `{summary, generatedBy, generatedAt}` |
| `session.recent` | L2 | `{id, n=20, beforeCursor?}` | `{messages: NormalizedMessage[], cursor}` |
| `session.grep` | L3 | `{id, query, mode="keyword", limit?}` | `{matches: [{messageId, offset, role, snippet}]}` |
| `session.stream` | L4 | `{id, fromCursor?}` | SSE stream of `{message, cursor}` events, terminating `{done:true}` |
| `session.handoff` | push | `{id, levels=["summary","recent"], n?}` | `HandoffPacket` |

### Schemas

```jsonc
// SessionManifest (L0) — lightweight, no message bodies
{
  "id": "…", "tool": "claude-code", "title": "…", "project": "…",
  "startedAt": "…", "endedAt": "…", "messageCount": 84,
  "tokens": {"input": …, "output": …},
  "hasSummary": true, "visibility": "company|team|private",
  "source": "local|hub"
}

// HandoffPacket (push) — the one-shot bundle
{
  "protocol": "aicp/0.1",
  "session": <SessionManifest>,
  "summary": "…",                 // L1
  "recent":  [<NormalizedMessage>],// L2 (last n)
  "more": { "grep": "session.grep", "stream": "session.stream" },
  "issuedAt": "…", "issuedBy": "…", "redacted": true
}
```

`filters` = `{tool?, project?, since?, until?, author?, q?}`. `cursor` is an opaque base64 token.

### Errors
A small, stable error set returned in the binding's native error channel: `not_found`, `forbidden` (visibility/consent/token), `consent_required` (carries a hint to prompt the owner), `invalid_cursor`, `rate_limited`, `internal`.

---

## 7. Data flows

**A. Local cross-agent handoff (v1 target).** In Codex: "continue the auth refactor I did in Claude Code." Codex → local broker: `session.search("auth refactor")` → `session.summary(id)` → `session.recent(id)`; it pulls `grep`/`stream` only if it needs more. Lazy and context-budget-aware.

**B. Cloud cross-dev pull (v2).** A teammate's agent → cloud broker with a capability token scoped to `summary+recent` for sessions matching a query; the hub enforces visibility.

---

## 8. Trust, consent, redaction

- **Always:** every exchange honors the session's **visibility** (private/team/company), and **redaction** runs before any bytes leave (reuses `apps/desktop/src/lib/redact.ts`).
- **Same machine (same user) — v1:** the first time a connecting agent calls a read verb, the desktop app shows a **one-time local grant** ("Allow *Codex* to read your Context Hub sessions?"), stored per client. No token. Extends the existing AI-consent UI. Returns `consent_required` until granted.
- **Cross-user / remote — v2:** **scoped capability tokens** `{sessions|query, maxLevel: summary|recent|grep|full, expiry}`; the cloud broker validates the token and enforces visibility.

---

## 9. Streaming & resumability

`session.stream` emits **one NormalizedMessage per SSE event**, each carrying a monotonic **cursor** (opaque base64 of the message sequence index). The client resumes after interruption by passing `fromCursor`. Message-level chunking, strictly ordered, backpressure via SSE. The same mechanism serves both "stream the whole session" and "resume from where I stopped." A terminating `{done:true}` event marks completion.

---

## 10. v1 scope & components

**Build first: the local broker MCP server inside the Tauri desktop app**, over the already-parsed local sessions.

- **MCP server** (stdio + optional local HTTP/SSE) implementing all verbs.
  - L0 `list` + keyword `search`; L1 `summary` via the existing LLM layer (consent-gated); L2 `recent`; L3 `grep` (keyword); L4 `stream` (NDJSON/SSE, resumable); `handoff`.
  - Semantic `search`/`grep` is a fast-follow (reuse the hub's embeddings); v1 ships keyword.
- **Agent-access grant UI** in the desktop app: list connected agents, grant/revoke (extends the consent modal + settings store: `aiConsent` → add `agentGrants`).
- **MCP config snippet generator**: a Settings panel that emits the exact one-line block to paste into Claude Code / Codex MCP config, pointed at the local broker.
- **Reuse:** parsers (normalized sessions), `redact.ts`, the LLM layer (summaries). Little new backend.

**Implementation note (resolved during planning):** the MCP server can live as (a) a Rust task inside the Tauri process, or (b) a bundled sidecar binary the app supervises. The sidecar (b) is preferred because it can keep serving when the GUI window is closed; the planning step will choose and sequence this.

**Deferred to v2 (designed above, not built now):** cloud broker MCP+REST surface, capability tokens, cross-user sharing, semantic in-session grep, A2A binding, hub-to-hub federation.

---

## 11. Testing

- **Conformance:** each verb's request/response validated against JSON schemas; the stable error set.
- **Resumability:** `stream` interrupted mid-way and resumed via `fromCursor` reproduces the exact remaining messages in order, no dupes/gaps.
- **Consent gating:** read verbs return `consent_required` before a grant and succeed after; redaction applied to all outbound payloads.
- **Integration:** a real MCP client connects to the local broker and runs the full ladder `list → summary → recent → grep → stream` against fixture sessions.

---

## 12. Open questions for the plan
- Sidecar vs in-process MCP server (see §10) — decide and sequence in the plan.
- Whether v1 `search` indexes locally (e.g. SQLite FTS over parsed sessions) or defers all ranking to keyword scan; depends on local session volume.
