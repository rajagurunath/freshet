# PR Context Links — Context Hub

Context Hub can attach a token-gated "agent context" page to any GitHub PR.
The page shows the session that produced the PR: the session title, a summary
of the reasoning ("why this PR"), linked PR/issue URLs, and the full decision
timeline with tool calls collapsed for readability.

---

## How it works

```
Agent session (local)
    │
    ▼
Desktop app: parse → redact → push to hub
    │
    ▼
Hub (POST /v1/sessions)
    │
    ▼
Hub: POST /v1/sessions/{id}/share → { url, token }
    │
    ▼
Script: gh pr comment + gh pr edit  →  PR now links to context page
    │
    ▼
Reviewer: opens context page URL — no hub account needed (token-gated)
```

### Token security

The share URL is protected by an HMAC-SHA256 token over `(session_id, expiry)`.
The token is valid for **24 hours** by default.  Anyone with the URL can view
the page — treat it as a secret link.  Set `SHARE_TOKEN_SECRET` in the hub's
environment (default `changeme-share-token-secret` — change this in production).

---

## Quick start

### 1. Push your session to the hub

After your agent session completes, use the desktop app to push it to the hub,
or call the API directly:

```bash
curl -s -X POST http://localhost:8787/v1/sessions \
  -H "Authorization: Bearer <your-key>" \
  -H "Content-Type: application/json" \
  -d @session_envelope.json
```

### 2. Attach the context link to a PR

```bash
# Install the helper (once)
export PATH="$PATH:/path/to/context-hub/scripts"

# Attach to PR #42 — auto-detects the most recent session for the current repo
contexthub-pr 42

# Or specify a session id explicitly
contexthub-pr 42 --session <session-id>

# Against a remote hub
contexthub-pr 42 --hub https://hub.example.com
```

Environment variables used by the script:

| Variable           | Default                  | Purpose                        |
|--------------------|--------------------------|--------------------------------|
| `CONTEXT_HUB_URL`  | `http://localhost:8787`  | Hub base URL                   |
| `CONTEXT_HUB_KEY`  | `dev-key`                | API bearer token               |

### 3. What the script does

1. Resolves the session (most recent session whose `project` matches the repo
   name, or `--session` override).
2. Calls `POST /v1/sessions/{id}/share` to mint a 24-hour context URL.
3. Posts a `gh pr comment` with the link and a short description.
4. Appends the URL to the PR body via `gh pr edit` (idempotent — no duplicate
   if run twice).

---

## API reference

### POST `/v1/sessions/{id}/share`

Mint a share token for a session context page.

**Auth:** Bearer token required (visibility enforced — you must be able to read the session).

**Response:**
```json
{
  "url": "http://hub/c/session-id?t=<token>&expiry=<unix>",
  "token": "<hmac-hex>",
  "expiry": 1749600000
}
```

### GET `/c/{session_id}?t=<token>&expiry=<n>`

Render the context page.

**Auth:** Token-gated — no Bearer auth needed.  Returns `403` for missing,
expired, or invalid tokens.

**Response:** `text/html` — a self-contained styled page showing:

- Session title and author
- "Why this PR" section (session summary)
- PR / issue / doc links from `session.links[]`
- Decision timeline: assistant prose messages shown inline; tool messages
  inside `<details>` elements (collapsed by default)
- Related knowledge graph nodes (optional, if graph extraction has run)

### GET `/v1/sessions?link=<pr-url>`

Find all sessions that contain a specific PR (or other) URL in their
`links[]` array.

```bash
curl -s "http://localhost:8787/v1/sessions?link=https://github.com/org/repo/pull/42" \
  -H "Authorization: Bearer <key>"
```

---

## Attaching links from an agent

When your agent pushes a session, include the PR URL in `session.links`:

```json
{
  "session": {
    "id": "...",
    "links": [
      {
        "kind": "pr",
        "url": "https://github.com/org/repo/pull/42",
        "label": "PR #42"
      }
    ]
  }
}
```

The hub stores the links and the context page renders them automatically.
`GET /v1/sessions?link=<url>` can later find all sessions attached to that PR.

---

## Agent workflow (fest #6 / #6.1)

The full recommended flow for agent-authored PRs:

1. **Agent works** → session accumulates in `~/.claude/projects/<repo>/`.
2. **Desktop auto-sync** (or manual push) → session ingested at the hub.
3. **Agent or CI calls `contexthub-pr <n>`** → PR receives the context link.
4. **Reviewer clicks the link** → sees the full reasoning without needing a
   hub account or API key.
5. **Hub search** → `GET /v1/sessions?link=<pr-url>` lets teammates find the
   session later when debugging or building on top of this PR.
