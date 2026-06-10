# Context Hub v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the architectural weaknesses in the current Context Hub (vector-only search, blocking ingest, unenforced visibility, contract drift, fictional auto-sync, dropped `/compact` data) and implement all 11 expectations from `fest.md`: landing page, branch-from-turn, sorting, auto-sync with batch summarization, hybrid search, asset hub (OpenSharing), PR context links, summary/detail views, compact awareness, knowledge graph, rules extraction, and subscription-window harvesting.

**Architecture:** Keep the two-deployable shape (Tauri desktop + FastAPI hub) but add three structural pieces: (1) a **versioned shared contract** generated from Pydantic so TS can never drift; (2) an **async jobs subsystem** in the API so summarization/embedding/graph-extraction run off the request path (this is also what batch summarization, rules mining, and the subscription harvester plug into); (3) a **knowledge layer** (graph nodes/edges + rules + assets tables) alongside the existing chunks/sessions tables. Retrieval becomes hybrid (LanceDB FTS + vector, RRF-merged). External asset sharing adopts the OpenSharing protocol's Skill/Volume asset-type REST surface.

**Tech Stack:** FastAPI, Pydantic v2, LanceDB (vector + FTS), SQLite (jobs/graph/rules/assets via `sqlite3` stdlib), boto3/S3, Anthropic + OpenAI (incl. Batch API) + CLI providers; Tauri 2 (Rust `notify` watcher), React 18, Zustand, React Router, `@tanstack/react-virtual`; static Vite landing page.

**Ground rules for every implementer:**
- TDD: failing test → minimal impl → green → commit. API tests: `cd apps/api && python -m pytest -q`. Desktop: `cd apps/desktop && npx tsc --noEmit && npx vitest run`.
- Commit per task with conventional messages (`feat:`, `fix:`, `refactor:`). **Never add any `Co-Authored-By` trailer.** `git add` only the files you touched. If `git commit` fails on `.git/index.lock`, wait 2s and retry (other agents commit concurrently).
- Follow `docs/DESIGN.md` for all UI (warm paper palette, hairline borders, no gradients/shadows, lucide icons).
- Pydantic v2 models ignore unknown fields by default — additive contract changes are backward compatible.

---

## Findings being fixed (from codebase audit, 2026-06-10)

**API (`apps/api`):** vector-only ANN search, no FTS/rerank; `GET /v1/sessions` unpaginated (unfiltered path full-scans `to_arrow().to_pylist()`); `ingest_session` runs `summarize_session()` synchronously (up to 180s `subprocess.run`); upsert is non-atomic delete-then-add with bare `except Exception: pass` (`storage/vectors.py:127-149`); re-ingest re-embeds/re-summarizes unconditionally and resets `created_at` (`routes.py:126`); `visibility` stored but never enforced; static comma-separated `API_KEYS` with no user identity; `models` column hand-rolled JSON string; `GET /v1/sessions/{id}` has no `response_model`; singleton `get_vector_store()` global; only smoke tests.

**Desktop (`apps/desktop`):** single hardcoded sort (newest-first at scan time, `parsers/index.ts:113`); claude parser silently drops `type:"summary"` compact lines (`parsers/claude.ts:251`); auto-sync settings exist with zero implementation; full re-scan + full-file parse on renderer thread every load, no mtime cache; no virtualization (`SessionDetailPage.tsx:301`); `pushedIds` never reconciled with server; TS↔Py contract synced by comment only; kilo parser never reads `task_metadata.json` so `cwd`/`project` are always undefined (`kilo.ts:165`); `SessionRow.tsx:8-23` duplicates helpers that exist in `lib/format.ts`; only in-app `WelcomeHero`, no landing page.

**OpenSharing research:** https://github.com/OpenSharing-IO/OpenSharing — LF AI & Data spec (Apache 2.0, donated by Databricks, launched 2026-06-10). Share → Schema → Asset hierarchy; asset types Table/Volume/**Agent Skill**/Model; bearer-token REST + temporary-credential vending; pull-only. Decision: implement its **Agent Skill + Volume** read surface for the asset hub (Task 15); do NOT use it for live session sync; ignore the unstable Agent/Page proposals.

---

## Phase 0 — Contract & API foundation

### Task 1: Versioned shared contract + TS generation
**Files:** Modify `apps/api/contexthub/models.py`; Create `apps/api/scripts/export_schema.py`, `apps/desktop/scripts/gen-types.mjs`, `apps/desktop/src/lib/contract.gen.ts`; Modify `apps/desktop/src/lib/types.ts`, `apps/desktop/package.json`; Test `apps/api/tests/test_contract.py`, `apps/desktop/src/lib/contract.test.ts`.
- [ ] Add to `NormalizedSession` (Py and TS): `schema_version: int = 2`, `compacted: bool = False`, `compact_summary: str | None`, `parent_session_id: str | None` (branch lineage), `branch_point_message_id: str | None`, `links: list[SessionLink] = []` where `SessionLink = {kind: "pr"|"issue"|"doc"|"session", url: str, label: str | None}`.
- [ ] `export_schema.py`: dump `IngestRequest.model_json_schema()` (+ Query/Summarize models) to `apps/api/schema/contract.json`; `gen-types.mjs` converts it to `contract.gen.ts` (hand-rolled ~80-line JSON-Schema→TS emitter is fine; no new deps). `types.ts` re-exports the generated session/envelope types, keeping desktop-only types (e.g. `SessionSummary`) local.
- [ ] Parity test: `test_contract.py` asserts the checked-in `contract.json` matches a fresh export (fails when models change without regen); `contract.test.ts` type-checks a sample fixture against generated types. Add `npm run gen:types` script.
- [ ] Commit.

### Task 2: Ingest hardening + pagination + sorting
**Files:** Modify `apps/api/contexthub/storage/vectors.py`, `apps/api/contexthub/api/routes.py`, `apps/api/contexthub/models.py`; Test `apps/api/tests/test_ingest.py`.
- [ ] Idempotency: compute `content_hash = sha256(canonical session JSON + summary)`, store on the `sessions` row. On re-ingest with identical hash: skip embedding + summarization, return `IngestResponse(..., skipped=True)`. Preserve original `created_at` on re-ingest (read existing row first); add `updated_at`.
- [ ] Atomic upsert: replace delete-then-add with `tbl.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(rows)`. Remove every bare `except Exception: pass` in `vectors.py`; log and re-raise (or return typed errors). `stats()` must not mask exceptions.
- [ ] `GET /v1/sessions`: add `limit` (default 50, max 200), `offset`, `sort` (`created_at|message_count|tokens_total|project|tool`), `order` (`asc|desc`); return `{items, total, limit, offset}` (`SessionPage` model). Add `tokens_input`/`tokens_output` int columns to the `sessions` table (sortable; stop JSON-stringifying `models` — use a LanceDB `list<string>` column). Add `response_model` to `GET /v1/sessions/{id}` (`SessionDetail` model).
- [ ] Tests: re-ingest same session → `skipped=True`, `created_at` unchanged, no duplicate chunks; pagination math; sort by tokens.
- [ ] Commit.

### Task 3: Hybrid search (FTS + vector + RRF)
**Files:** Modify `apps/api/contexthub/storage/vectors.py`, `apps/api/contexthub/rag/agent.py`, `apps/api/contexthub/models.py` (`QueryRequest.mode: "hybrid"|"vector"|"keyword" = "hybrid"`); Test `apps/api/tests/test_search.py`.
- [ ] Create FTS index on `chunks.text` lazily: `tbl.create_fts_index("text", replace=True, use_tantivy=False)` (native LanceDB FTS; re-create after batch upserts via an index-freshness check, not per-request).
- [ ] `VectorStore.hybrid_search(query, query_vec, top_k, filters)`: run vector search and FTS search (each `limit=top_k*3`, same `where` filters), merge with Reciprocal Rank Fusion:
  ```python
  def rrf(rank_lists, k=60):
      scores = defaultdict(float)
      for ranks in rank_lists:
          for i, row_id in enumerate(ranks):
              scores[row_id] += 1.0 / (k + i + 1)
      return sorted(scores, key=scores.get, reverse=True)
  ```
  Return top_k rows with fused score. Keyword-only and vector-only modes reuse the same path.
- [ ] `answer_query()` uses hybrid by default; citations carry the fused score. Tests with the `hash` embedder: an exact-identifier query ("ERR_5021_FOO") must retrieve the chunk containing it even when vector search alone would not (hash embedder guarantees that mismatch).
- [ ] Commit.

### Task 4: Identity + visibility enforcement
**Files:** Modify `apps/api/contexthub/config.py`, `apps/api/contexthub/deps.py`, `apps/api/contexthub/api/routes.py`, `apps/api/contexthub/storage/vectors.py`; Test `apps/api/tests/test_visibility.py`.
- [ ] Extend `API_KEYS` format to `key:user_id:team` triples (comma-separated; bare `key` still accepted → anonymous full-company access, warn at startup). `require_api_key` returns a `Caller{user_id, team}` injected into routes.
- [ ] Add `team: str | None` to the envelope/author and `sessions`/`chunks` rows. Enforce on every read path (list, get, query/search): `visibility == 'company' OR (visibility == 'team' AND team == caller.team) OR (visibility == 'private' AND author == caller.user_id)` as a LanceDB `where` clause.
- [ ] Tests: private session invisible to another caller in list, get (404), and query citations.
- [ ] Commit.

### Task 5: Jobs subsystem (async work off the request path)
**Files:** Create `apps/api/contexthub/jobs/__init__.py`, `apps/api/contexthub/jobs/store.py` (SQLite), `apps/api/contexthub/jobs/worker.py`, `apps/api/contexthub/jobs/handlers.py`; Modify `apps/api/contexthub/main.py` (lifespan starts/stops worker), `apps/api/contexthub/api/routes.py`, `apps/api/contexthub/models.py`; Test `apps/api/tests/test_jobs.py`.
- [ ] `jobs` SQLite table: `id, kind, payload_json, status(queued|running|done|error), result_json, error, created_at, started_at, finished_at, scheduled_for`. `JobStore` with `enqueue/claim_next/complete/fail/get/list` (WAL mode, `claim_next` uses `UPDATE ... WHERE id = (SELECT ...) RETURNING` for single-worker safety).
- [ ] Worker: asyncio task started in lifespan; loop `claim_next → await asyncio.to_thread(handler, payload) → complete/fail`; honors `scheduled_for` (skip future jobs). Handler registry keyed by `kind`.
- [ ] Move summarization out of ingest: `POST /v1/sessions` with `summarize=true` enqueues `kind="summarize_session"` and returns immediately (`IngestResponse.job_id`). Handler summarizes then updates the session row's `summary` + re-embeds chunk 0. Add `GET /v1/jobs/{id}` and `GET /v1/jobs?status=&kind=`.
- [ ] Tests: enqueue → worker processes (use a registered test handler) → status transitions; ingest returns fast with job_id.
- [ ] Commit.

---

## Phase 1 — Desktop UX

### Task 6: `/compact` awareness in parser + UI
**Files:** Modify `apps/desktop/src/lib/parsers/claude.ts`, `apps/desktop/src/lib/types.ts` (the new contract fields exist after Task 1), `apps/desktop/src/components/MessageBubble.tsx`, `apps/desktop/src/pages/SessionDetailPage.tsx`, `apps/desktop/src/components/SessionRow.tsx`; Test `apps/desktop/src/lib/parsers/parsers.test.ts`.
- [ ] In `claude.ts`, stop dropping `type:"summary"` lines: capture `summary` text → `session.compacted = true`, `session.compactSummary = <text>` (last one wins). Also detect `isCompactSummary`/`compact` markers on user messages (Claude Code emits the post-compact continuation as a user message containing "This session is being continued from a previous conversation" — flag it `kind: "compact-marker"` on the message).
- [ ] Insert a visible compaction divider in the transcript where the marker occurs ("— context compacted here — summary ↓" collapsible showing `compactSummary`). `SessionRow` shows a small "compacted" badge.
- [ ] `SessionDetailPage`: pre-fill the AI-summary textarea with `compactSummary` when present (cheaper than calling the LLM; user can still regenerate).
- [ ] Tests: fixture JSONL with a summary line + continuation message → `compacted=true`, summary captured, marker message present.
- [ ] Commit.

### Task 7: Sorting & filtering for the sessions list
**Files:** Modify `apps/desktop/src/pages/SessionsPage.tsx`, `apps/desktop/src/store/app.ts`, Create `apps/desktop/src/lib/pricing.ts`; Test `apps/desktop/src/lib/pricing.test.ts`.
- [ ] `pricing.ts`: model→$/Mtok map (claude opus/sonnet/haiku 4.x, gpt-4o/5.x families; export `estimateCost(session)` returning `{usd, known: boolean}`; unknown models → input/output at a default rate, `known=false`, render with `~` prefix).
- [ ] Sort control (Select, persisted in store): date | project | tool | tokens | cost | messages; asc/desc toggle. Filters: project dropdown (derived), date range (last 7/30/90/all), "compacted only" toggle, existing tool tabs + text search. All client-side `useMemo`.
- [ ] Show token count + estimated cost on `SessionRow` (mono font, `--ink-faint`); delete the duplicated inline helpers in `SessionRow.tsx:8-23` and import from `lib/format.ts`.
- [ ] Commit.

### Task 8: Performance + summary/detail views
**Files:** Modify `apps/desktop/src/lib/parsers/index.ts`, `apps/desktop/src/store/app.ts`, `apps/desktop/src/pages/SessionDetailPage.tsx`, `apps/desktop/package.json` (add `@tanstack/react-virtual`); Test `apps/desktop/src/lib/scan-cache.test.ts`.
- [ ] Incremental scan: persist `{filePath: {mtime, size, sessionId}}` cache (localStorage via zustand persist); `scanLocalSessions()` takes the cache, re-parses only new/changed files, returns merged result + updated cache. Expose `rescan()` on the store; call on window focus instead of only on mount.
- [ ] Virtualize the transcript with `useVirtualizer` (estimate 120px, `measureElement` for dynamic heights); virtualize the sessions list when > 100 rows.
- [ ] Detail page Tabs: **Summary** (default when a summary/compactSummary exists: summary text, KPI strip, tool-use breakdown, files touched if derivable from tool messages) | **Transcript** (full virtualized thread). Keep Curate & Push panel on both.
- [ ] Commit.

### Task 9: Branch-from-turn (Opus)
**Files:** Create `apps/desktop/src/lib/branch.ts`; Modify `apps/desktop/src/components/MessageBubble.tsx`, `apps/desktop/src/pages/SessionDetailPage.tsx`, `apps/desktop/src-tauri/capabilities/default.json` (add scoped **write** permission for `$HOME/.claude/projects/**` only), `apps/desktop/src/lib/tauri.ts` (add `writeText`); Test `apps/desktop/src/lib/branch.test.ts`.
- [ ] `branchSession(session, messageId)` (claude-code sessions only, v1): read the original JSONL, keep raw lines up to and including the line whose message id == messageId, rewrite `sessionId` fields to a fresh UUID v4 on every kept line, write to `<same project dir>/<newId>.jsonl`. Set `parent_session_id`/`branch_point_message_id` in the parsed result so lineage survives a later push. Non-claude tools: hide the action (tooltip "Claude Code only for now").
- [ ] UI: hover action "Branch from here" on user/assistant bubbles → confirm modal → writes file → success toast with copyable `claude --resume <newId>` command and a "Reveal in sessions" link (rescan picks it up). Detail header shows "Branched from <parent title>" chip when `parentSessionId` is set, linking to the parent.
- [ ] Tests: fixture JSONL → branch at message 3 → new file has exactly the prefix lines, new uuid everywhere, original untouched.
- [ ] Commit.

---

## Phase 2 — Sync & batch

### Task 10: Real auto-sync engine
**Files:** Modify `apps/desktop/src-tauri/src/lib.rs` + `Cargo.toml` (add `notify` crate, emit `session-file-changed` events), `apps/desktop/src/lib/tauri.ts`, Create `apps/desktop/src/lib/autosync.ts`; Modify `apps/desktop/src/App.tsx`, `apps/desktop/src/pages/SettingsPage.tsx`, `apps/desktop/src/store/app.ts`; Test `apps/desktop/src/lib/autosync.test.ts`.
- [ ] Rust: `start_watching(roots)` command using `notify::recommended_watcher`, debounced 2s, emits Tauri events with the changed path. JS fallback (browser/mock mode): 60s polling of `listSessionFiles` mtimes.
- [ ] `autosync.ts`: on event → if `settings.syncMode === "auto"` and tool ∈ `autoSyncTools` → wait for quiet period (no writes for 5 min = session likely closed) → parse, redact (always, when `redactBeforePush`), push with defaults, `markPushed`. Maintain a per-file `lastSyncedHash` so edits re-push idempotently (server dedupes via Task 2). Surface a sync-status row in Settings (last run, queue length, errors) + a small "auto" badge in the sidebar when active.
- [ ] Reconcile `pushedIds` on hub connect: `GET /v1/sessions?author=me&limit=200` → drop stale local ids.
- [ ] Tests: fake event stream → quiet-period logic, tool filtering, hash skip.
- [ ] Commit.

### Task 11: Batch summarization (OpenAI Batch API + local model)
**Files:** Create `apps/api/contexthub/llm_batch.py`; Modify `apps/api/contexthub/jobs/handlers.py`, `apps/api/contexthub/config.py`, `apps/api/contexthub/api/routes.py`, `apps/api/contexthub/llm.py` (add `ollama`/openai-compatible local provider with `base_url`); Test `apps/api/tests/test_batch.py`.
- [ ] `POST /v1/summarize/batch {session_ids[], provider: "openai-batch"|"local"|"default"}` → enqueues `kind="summarize_batch"`.
- [ ] `openai-batch` handler: build JSONL of `/v1/chat/completions` requests (one per session, transcript truncated 40k chars), upload via `client.files.create(purpose="batch")`, `client.batches.create(completion_window="24h")`, store `batch_id` in job result, re-enqueue a `kind="batch_poll"` job with `scheduled_for=now+10min`; on completion, parse output file, write each summary to its session row, re-embed summary chunks. (This is the cheap path: OpenAI Batch is ~50% of sync pricing.)
- [ ] `local` handler: loop sessions through the openai-compatible provider pointed at `LOCAL_LLM_BASE_URL` (e.g. Ollama) synchronously inside the job — free when the machine has resources.
- [ ] Desktop hook-in (small): auto-sync (Task 10) sets `summarize=false` on push; Settings gains "Hub summarization: per-push | nightly batch | local model" which the desktop sends as envelope hint `summary_mode`; a nightly `kind="summarize_pending"` job (enqueued by worker on startup if absent, `scheduled_for` next 02:00) batches all summary-less sessions.
- [ ] Tests: mock OpenAI client; batch JSONL shape, poll→complete flow, summary persisted.
- [ ] Commit.

### Task 12: Subscription-window harvester (experimental)
**Files:** Create `apps/api/contexthub/jobs/harvest.py`; Modify `apps/api/contexthub/config.py`, `apps/api/contexthub/jobs/handlers.py`, `apps/api/contexthub/api/routes.py` (`GET /v1/harvest/status`); Test `apps/api/tests/test_harvest.py`.
- [ ] Config: `HARVEST_ENABLED=false`, `HARVEST_PROVIDERS=claude-cli,codex-cli`, `HARVEST_WINDOW_RESET="mon 00:00"` (cron-ish: weekday + time, parsed by hand), `HARVEST_LOOKAHEAD_HOURS=12`.
- [ ] Recurring `kind="harvest_check"` job (hourly): if within lookahead of the window reset, drain pending work (summary-less sessions → summarize via the CLI provider; sessions lacking graph extraction → enqueue Task 13 jobs) until none remain or the provider starts erroring (rate-limit ⇒ stop, reschedule +30min). Rationale: burn the unused weekly Claude/Codex subscription quota on summarization/graph work right before it resets (fest #11).
- [ ] Status endpoint reports next reset, pending counts, last drain results. Document honestly in the code: usage-remaining is not detectable via CLI, so this is time-based best-effort.
- [ ] Tests: fake clock → drains only inside lookahead; stops on provider error.
- [ ] Commit.

---

## Phase 3 — Knowledge layer

### Task 13: Knowledge graph / GraphRAG-lite (Opus)
**Files (API):** Create `apps/api/contexthub/graph/store.py` (SQLite: `nodes(id, kind, name, summary)` kinds = repo|service|feature|person|decision|tool|pr; `edges(src, dst, rel, session_id, weight)`), `apps/api/contexthub/graph/extract.py`; Modify `apps/api/contexthub/jobs/handlers.py` (`kind="graph_extract"`, enqueued after ingest), `apps/api/contexthub/api/routes.py` (`GET /v1/graph?focus=&depth=`, `GET /v1/graph/session/{id}`, graph-augmented `POST /v1/query` when `use_graph=true`); Test `apps/api/tests/test_graph.py`.
**Files (Desktop):** Create `apps/desktop/src/pages/GraphPage.tsx` (+ route `/graph`, sidebar entry); render with a dependency-free SVG force layout (~120 lines: simple spring simulation, nodes colored by kind per DESIGN.md chips, click → side panel listing linked sessions).
- [ ] `extract.py`: LLM prompt over summary (fallback: first 6k chars of transcript) returning strict JSON `{nodes:[{kind,name,summary}], edges:[{src,dst,rel}]}`; validate, normalize names (lowercase, trim), upsert with dedup by `(kind, name)`; every node/edge keeps `session_id` provenance. Same feature touched by two repos' sessions ⇒ shared `feature` node links them — this is the cross-microservice/marketing linkage from fest #9.
- [ ] Graph-augmented query: when `use_graph=true`, after hybrid search also match question terms against node names, pull 1-hop neighbors, append "Knowledge graph context" block (nodes + relations + their session ids) to the LLM context.
- [ ] Tests: extraction with a mocked LLM returning fixture JSON → nodes deduped across two sessions sharing a feature; `/v1/graph` shape; visibility enforced (graph rows carry visibility from their session).
- [ ] Commit (API and desktop separately).

### Task 14: Rules extraction with consent
**Files (API):** Create `apps/api/contexthub/rules/extract.py`, rules SQLite table (`rules(id, text, rationale, evidence_json, scope, status proposed|accepted|rejected, created_at)`); Modify `apps/api/contexthub/jobs/handlers.py` (`kind="rules_extract"` over the author's last N summaries/transcripts), `apps/api/contexthub/api/routes.py` (`GET /v1/rules?status=`, `POST /v1/rules/{id}/accept|reject`, `GET /v1/rules/export` → markdown CLAUDE.md-style block of accepted rules); Test `apps/api/tests/test_rules.py`.
**Files (Desktop):** Create `apps/desktop/src/pages/RulesPage.tsx` (+ route `/rules`, sidebar entry): proposed-rule cards (rule text, rationale, evidence session links, Accept/Reject buttons), accepted list with "Copy as CLAUDE.md" button, "Mine rules now" button (enqueues the job).
- [ ] Extraction prompt: "From these session excerpts, identify recurring user preferences/conventions (commit style, naming, review habits, tooling choices). Return JSON rules with 1-line rationale and the session ids that evidence each." Dedup against existing rules by normalized text similarity (lowercased token overlap > 0.8 ⇒ skip).
- [ ] Consent is explicit: nothing is exported unless status=accepted (fest #10's "based on consent").
- [ ] Commit (API and desktop separately).

---

## Phase 4 — Sharing & integration

### Task 15: Asset hub + OpenSharing-compatible surface
**Files (API):** Create `apps/api/contexthub/assets/store.py` (SQLite `assets(id, kind skill|script|config|prompt, name, description, category, author, team, visibility, files_json, blob_uri, version, created_at)` — file payloads zipped to the blob store), `apps/api/contexthub/api/opensharing.py`; Modify `apps/api/contexthub/api/routes.py` (`POST /v1/assets` multipart upload, `GET /v1/assets?kind=&category=&q=` with FTS over name+description, `GET /v1/assets/{id}`, `GET /v1/assets/{id}/download`); Test `apps/api/tests/test_assets.py`.
- [ ] OpenSharing read surface (per https://github.com/OpenSharing-IO/OpenSharing spec/protocols/AGENT_SKILLS.md): `GET /opensharing/shares`, `GET /shares/{share}/schemas`, `GET /shares/{share}/schemas/{schema}/skills` (+ all-skills), `POST .../skills/{skill}/temporary-skill-credentials`. One share `company`, schemas = our categories, skills = assets of kind=skill. Credential vending: S3 mode → STS-less presigned URLs in the volume/skill response; local mode → short-lived signed download token (`hmac(asset_id + expiry)`). Bearer auth reuses our API keys.
**Files (Desktop):** Create `apps/desktop/src/pages/AssetsPage.tsx` (+ route `/assets`, sidebar entry), `apps/desktop/src/lib/assetScan.ts`: scan `~/.claude/skills/**`, `~/.claude/commands/**`, `~/.claude/agents/**` (extend Tauri read capability for those globs) → list local skills/commands with "Push to hub" (zips dir contents into the multipart upload, category select); hub tab browses/searches company assets with download.
- [ ] Tests: upload→list→download roundtrip; OpenSharing listing matches spec field names (`name`, `schema`, `share`); signed token expiry honored.
- [ ] Commit (API and desktop separately).

### Task 16: PR context links
**Files (API):** Modify `apps/api/contexthub/api/routes.py`, Create `apps/api/contexthub/api/context_page.py`; Test `apps/api/tests/test_context_page.py`. **Files (repo):** Create `scripts/contexthub-pr` (bash+python, executable), `docs/PR_CONTEXT.md`.
- [ ] `POST /v1/sessions/{id}/share` → `{url, token}` where token = `hmac_sha256(secret, session_id + expiry)`; `GET /c/{session_id}?t=...` returns a server-rendered HTML context page (no auth cookie needed — token-gated): session title, author, "why this PR" = summary, decision timeline (assistant prose messages, tool noise collapsed), links, graph neighbors if present. Style inline per DESIGN.md.
- [ ] Session `links[]` (from Task 1) lets the desktop/agent attach the PR URL; the context page shows it and `GET /v1/sessions?link=<pr-url>` finds sessions by PR.
- [ ] `scripts/contexthub-pr <pr-number>`: resolves the current repo + session (most recent session whose cwd == repo root, or `--session <id>`), calls `/share`, then `gh pr comment <n> --body "🧠 **Agent context:** why this PR exists, the full thought process → <url>"` and appends the link to the PR body via `gh pr edit`. Document agent usage in `docs/PR_CONTEXT.md` (agent pushes session → mints link → adds to PR; fest #6/#6.1 flow).
- [ ] Tests: share token validates/expires; context page renders summary + collapses tool messages; bad token → 403.
- [ ] Commit.

---

## Phase 5 — Landing page

### Task 17: Landing page with protocol animation

> **Scope expanded 2026-06-11 (user request):** the landing page must sell the *complete product* in the style of modern YC devtool landings (reference: conductor.build) — full section sequence (hero with product visual, feature grid covering agent/graph/assets/PR-links/auto-sync, how-it-works, privacy/local-first trust, final CTA), scroll-reveal animations, and an HTML/CSS product UI mock — with the protocol animation as one section, not the whole page. Built from a design-research brief (conductor.build, linear.app, cursor.com, resend.com, et al.).
**Files:** Create `apps/web/index.html`, `apps/web/styles.css`, `apps/web/app.js`, `apps/web/README.md` (pure static, no build step; serve with `python -m http.server`); Modify root `README.md` (link), `Makefile` (`make landing`).
- [ ] YC-grade direct copy per DESIGN.md voice. Hero: "Everything your team taught the AI, in one place." Sub: one sentence on capture→curate→ask. Single accent CTA. Sections, one per use case, each with a small inline SVG: (1) capture local sessions, (2) ask the company agent (hybrid search), (3) PR context links, (4) knowledge graph, (5) asset hub / OpenSharing, (6) auto-sync + batch summarization.
- [ ] The animation (fest #0): hand-rolled SVG/CSS animation of the protocol interaction — desktop node → redact → push envelope → hub (S3 + index) → query from teammate → cited answer; animated dashes (`stroke-dashoffset` keyframes) + staged opacity, ~12s loop, `prefers-reduced-motion` respected. No JS animation libraries.
- [ ] Light Lighthouse hygiene: system font fallback before Inter, inline critical CSS, total page < 200KB.
- [ ] Commit.

---

## Verification (workflow phase, after all tasks)
- [ ] `cd apps/api && python -m pytest -q` green; `cd apps/desktop && npx tsc --noEmit && npx vitest run` green; regen contract (`python scripts/export_schema.py && npm run gen:types`) produces no diff.
- [ ] Code review pass over the full diff (bugs, security: HMAC tokens, visibility filters, path traversal in asset zips, command injection in `contexthub-pr`).
- [ ] Fix-up agents for any high-confidence findings; final commit.

## Explicitly deferred (recorded, not built now)
- SSO/OAuth (API-key triples are the MVP identity), cross-encoder reranking (RRF first; measure), OpenSharing Agent/Page asset types (spec marked "design not final"), Cursor/Cline parsers, dark mode.
