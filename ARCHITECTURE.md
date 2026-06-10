# Architecture

Context Hub has two deployables and one shared data contract.

```
┌──────────────────────────────────────────────────────────────────┐
│  DESKTOP APP  (Tauri 2 + React)         per-employee, local         │
│                                                                     │
│  ~/.claude ─┐                                                       │
│  ~/.codex  ─┼─▶ Local parsers ─▶ Normalized Session ─▶ Browse / View │
│  VS Code   ─┘     (TypeScript)         (contract)        Summarize   │
│                                                          Curate      │
│                                                            │         │
│                                            redact secrets  │ push    │
└────────────────────────────────────────────────────────────┼───────┘
                                                               │ HTTPS + API key
                                                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  CENTRAL API  (FastAPI)                         company-wide cloud  │
│                                                                     │
│   POST /v1/sessions ─▶ raw blob ─▶ S3                               │
│                     └▶ chunk ─▶ embed ─▶ LanceDB (vectors+metadata) │
│                                                                     │
│   POST /v1/query  ─▶ embed query ─▶ LanceDB search ─▶ Claude ─▶ answer
│   POST /v1/summarize ─▶ Claude                                      │
│   GET  /v1/sessions, /v1/categories, /v1/stats                      │
└──────────────────────────────────────────────────────────────────┘
```

## The shared contract: `NormalizedSession`

Both the local parsers and the API ingest endpoint speak this shape. It is the single source of truth. Defined in:
- TS: `apps/desktop/src/lib/types.ts`
- Py: `apps/api/contexthub/models.py`

```jsonc
{
  "id": "0f433f50-...",            // assistant-native session id
  "tool": "claude-code",           // claude-code | codex | kilo-code
  "title": "Fix the S3 upload retry bug",
  "cwd": "/Users/x/proj",
  "project": "proj",
  "started_at": "2026-06-01T18:16:18Z",
  "ended_at":   "2026-06-01T19:02:44Z",
  "message_count": 84,
  "models": ["claude-opus-4-8"],
  "tokens": { "input": 120344, "output": 18211 },
  "messages": [
    { "id":"m1", "role":"user", "text":"...", "timestamp":"...", "model":null },
    { "id":"m2", "role":"assistant", "text":"...", "thinking":"...", "model":"claude-opus-4-8" },
    { "id":"m3", "role":"tool", "tool_name":"Bash", "text":"..." }
  ],
  "preview": "first ~240 chars of the first user prompt",
  "file_path": "/Users/x/.claude/projects/.../id.jsonl"
}
```

On push, the desktop app adds an **envelope**:
```jsonc
{
  "session": { /* NormalizedSession, possibly redacted */ },
  "summary": "optional curated summary text",
  "category": "engineering",       // engineering | sales | marketing | research | ops | other
  "visibility": "company",         // company | team | private
  "author": { "id":"u_123", "email":"x@co.com", "name":"X" },
  "redacted": true
}
```

## Central API responsibilities
| Concern | Choice | Notes |
|---|---|---|
| Raw session storage | **S3** (`boto3`) | Key `sessions/{author}/{session_id}.json`. Falls back to local dir when no bucket configured. |
| Vector index | **LanceDB** | One `chunks` table (vector + metadata) and one `sessions` table (catalog). LanceDB URI can be local path or `s3://`. |
| Embeddings | **sentence-transformers** `all-MiniLM-L6-v2` (default, 384-d, no key) | Pluggable: `EMBEDDING_PROVIDER=local|openai`. |
| Summaries | **Claude** (`anthropic`) | `claude-sonnet-4-6` default; configurable. |
| RAG answer | **Claude** | Retrieve top-k chunks across the org → grounded answer with citations. |
| Auth | Bearer API key per user (env allowlist for MVP) | Pluggable to SSO later. |
| Config | `pydantic-settings` + `.env` | Everything has a sane local default. |

### Ingest pipeline
1. Receive envelope → validate.
2. Store raw JSON in S3 (or local blob store).
3. Build chunks: summary (if present) + sliding windows over `messages` text (skip pure tool-output noise, keep decisions/prose). Each chunk carries `{session_id, tool, category, author, project, role_span, text}`.
4. Embed chunks → upsert into LanceDB `chunks`.
5. Upsert catalog row into `sessions`.

### Query pipeline (the company agent)
1. Embed the question.
2. Vector search `chunks` (+ optional metadata filters: category/tool/project/date).
3. Assemble context with provenance.
4. Claude answers, citing the sessions it used. Returns `{answer, citations[]}`.

## Sync modes
- **Manual** (default): user reviews → summarizes → pushes selected sessions.
- **Auto**: desktop watches session dirs; on session close, redacts + pushes per the user's category rules. Toggle + per-tool/per-project rules in Settings.

## Security
- Local-first parsing; explicit push.
- Pre-upload secret redaction (regex pack: API keys, tokens, `.env`-style, PEM, JWTs) with a preview diff.
- Per-session visibility (`company`/`team`/`private`).
- API key auth; designed to slot SSO/SOC2 controls later.
