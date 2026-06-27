# GraphRAG + Cross-Session Semantic Correlation — Experiment Plan

**Status:** APPROVED (autonomous, opinionated — author away)
**Branch:** `experiment/graphrag-semantic-memory` (off `master` @ Slice-1 committed)
**Date:** 2026-06-27
**Companion docs:** [[context-hub-v2-plan]], `cross-session-graph-memory` memory, AICP spec
(`docs/superpowers/specs/2026-06-10-aicp-session-exchange-protocol-design.md`)

---

## 1. Thesis & the one defensible moat

We ingest a developer's AI-coding sessions (Claude Code, Codex, Kilo, …) **passively from disk**,
build a **cross-session knowledge graph over the conversation corpus**, and surface the right past
session(s) + context for a new question — even when a 6-hour session defeats keyword search. Optional
roll-up into a vendor-neutral, self-hostable **company memory** agent.

Competitive reality (research-backed, brutally honest):
- **Cross-tool memory is commoditizing** — Engram (manual writes), Cloudflare, and **memctl
  (archived May 2026: "native tool memory now covers this")**. Do **not** lead with it.
- **omnigent = Databricks** orchestration meta-harness (Apache-2.0), *not* memory — but Databricks
  owns the enterprise backend (Delta/MLflow/Unity + Agent Bricks Knowledge Assistant) we'd meet
  up-market. Our counter: vendor-neutral, local-first, self-hostable.
- **The ONE genuine moat = GraphRAG over the conversation corpus** (not code-graph like
  CodeGraph/Graphify; not a fact-KV like Zep/Mem0; not document-GraphRAG). Unoccupied — *because it
  is hard and skeptics believe good chunked vector search wins.* **So it must be PROVEN with
  benchmarks.** That makes the eval harness foundational, not optional.
- **Differentiators to keep:** passive disk ingestion (have it), conversation-corpus graph
  (building), vendor-neutral self-hostable hub (have it), **cross-developer rule propagation**
  (rule *extraction* is already done per-tool by Claude/Cursor — aggregation across devs is the edge).
- **Monetization:** OSS = local single-dev; **paid = team/org cloud-sync hub (seats)**, positioned as
  the non-Databricks, non-single-vendor org memory. Open-core.

**Build implication:** every retrieval slice is measured against the vanilla 2-arm (vector+FTS)
baseline. Headline number we are chasing: **Recall@10 / Hit@3 lift on long-session cross-session
questions, GraphRAG vs vanilla**. Prove it or drop it.

## 2. What already exists (baseline — do not rebuild)

- Union KG in SQLite `graph.db`: `nodes(kind,name UNIQUE)`, `edges(src,dst,rel UNIQUE, weight)`,
  `node_sessions` provenance. `same_as` soft-links (Slice 1: ngram-jaccard blocking + stdlib cosine
  on MiniLM of `"{kind}: {name}. {summary}"`). `get_canonical_id()` = BFS over `same_as`.
- Extraction `graph/extract.py`: **LLM-only** JSON {nodes,edges}; kinds repo|service|feature|person|
  decision|tool|pr.
- Retrieval `rag/agent.py` + `storage/vectors.py`: vector + FTS arms fused by `rrf(rank_lists,k=60)`.
  Graph is **only an appended text block** via `_build_graph_context()`, seeded by brittle term/LIKE
  match (`find_nodes_by_terms`). MiniLM (all-MiniLM-L6-v2) in LanceDB; `hash` embedder offline.
- Jobs queue (SQLite) with handler registry; `graph_extract` → enqueues `entity_resolve`.
- LLM pluggable (claude-cli default | codex-cli | anthropic | openai | local); lazy `get_llm()`.
- Tests: pytest, offline via `EMBEDDING_PROVIDER=hash`, tmp dirs, stub LLM/embedder. ~267 pass.

## 3. Technique decisions (from SOTA research — opinionated)

Throughline: **add signal on a different axis than embeddings.** Cross-encoder (token-level), graph
structure (entity-seeded traversal), co-occurrence (PPMI), temporal validity, MMR (diversity) add
retrieval power; clustering/BERTopic only add browsing UX.

**ADOPT (ranked impact/effort):**
1. **Cross-encoder rerank — FlashRank** (torch-free ONNX, 4–34MB, CPU). Rerank top-N of RRF. Biggest
   single retrieval win (+5–15 nDCG@10). Optional `[rerank]` extra, fallback = RRF order.
2. **Graph as 3rd RRF arm** — entity-seeded 1–2 hop traversal → ranked session_ids → `rrf([vec,fts,
   graph],k=60)`, modest graph weight. Reuses plumbing, no score normalization. **The moat in code.**
3. **Eval harness (native metrics)** — synthetic multi-session corpus + silver questions (gold =
   source session id; multi-session-synthesis arm = the real claim). Recall@k, Hit@k, MRR, nDCG
   implemented natively (no ranx dep). Measures every slice vs baseline.
4. **MMR context assembly + LightRAG tri-partite token budget** — ~15 lines numpy; per-arm
   sub-budgets (graph/vector/fts) so packed sessions aren't near-duplicates.
5. **NER hybrid extraction — spaCy EntityRuler + base NER, GLiNER optional** (user's explicit ask).
   EntityRuler = deterministic code entities (paths, `func()`, env vars, repo/service gazetteers);
   base = PERSON/ORG/PRODUCT; GLiNER medium-v2.1 (**Apache-2.0**) zero-shot optional. Runs BEFORE the
   LLM (cheap deterministic pass), LLM only for relations/disambiguation. Optional `[nlp]` extra,
   fallback = pure-regex code-entity extractor (always offline).
6. **Correlation beyond graph (explicit "what else"):** (a) **kNN session-similarity edges** (mutual-
   kNN over summary embeddings → "related sessions", a similarity graph complementary to the entity
   KG); (b) **PPMI entity co-occurrence** (scipy.sparse) to weight edges + as a fusion signal.
7. **Rules mining** — HDBSCAN (sklearn, present) cluster paraphrases → frequency counts (native
   fpgrowth-style, ≥3-session floor) → **LLM writes the prose rule** (never counts). Differentiator;
   cross-dev propagation later.
8. **Bi-temporal edge validity + LLM grey-band adjudication (Graphiti ideas)** — `valid_at/invalid_at`
   on edges, **invalidate-don't-delete** so superseded decisions stop resurfacing; LLM adjudicates the
   existing 0.55–0.85 dedup grey band.
9. **Leiden communities + lazy top-level summaries** — `leidenalg` optional `[graphrag]`; global/
   thematic questions only; LazyGraphRAG lesson = defer LLM to query time.

**SKIP / DEFER:** HyDE per-query (inconsistent, per-query LLM cost — use HyPE at index time if
needed); full BERTopic (UX-only, install-heavy, needs hundreds of sessions); MS GraphRAG global
map-reduce as default (cost cliff); adopting mem0/Zep infra wholesale (borrow ideas, not Neo4j).

## 4. Sliced build sequence (each: TDD → tests green → commit on branch)

Dependency-aware ordering; no-new-dep slices first to prove the moat fast.

- **S1 Eval harness** `eval/` — synthetic corpus generator (deterministic, planted entities across
  sessions), silver-question generator (single- & multi-session), native metrics, runner that scores
  a `retrieve(question)->[session_id]` callable. Baseline = vector+FTS. *Accept:* `python -m
  contexthub.eval.run` prints Recall@10/Hit@3/MRR/nDCG for baseline; deterministic; offline (hash ok).
- **S2 Node vectors + seeding** — `node_vectors` LanceDB table; `seed_nodes_from_query()` replaces
  term/LIKE seeding in `_build_graph_context`. *Accept:* semantic seeds (e.g. "auth"→"authentication")
  that LIKE misses; tests pass.
- **S3 Graph RRF arm** — `graph_search(question)->ranked session_ids` (seed nodes → 1–2 hop incl.
  `same_as` → rank by weight/PPR, cap hub blow-up) added as 3rd arm. *Accept:* 3-arm Recall@10 > 2-arm
  on the multi-session eval arm (the moat number); A/B logged.
- **S4 Cross-encoder rerank** — FlashRank over top-N RRF; optional, graceful fallback. *Accept:*
  nDCG@10 lift vs un-reranked on eval; absent-dep path = identity.
- **S5 NER hybrid extraction** — `graph/ner.py` (spaCy EntityRuler + base, GLiNER optional, regex
  fallback); wired into extract path before LLM. *Accept:* code entities (paths/func/env) extracted
  deterministically; graph density ↑; LLM-only still works when `[nlp]` absent.
- **S6 Correlation beyond graph** — kNN session edges + PPMI edge weighting; "related sessions"
  surface. *Accept:* related-session recall on a held-out planted pair; PPMI weights stored.
- **S7 MMR + tri-partite budget** — diverse context assembly. *Accept:* dedup reduces redundant
  near-identical sessions in packed context; budget respected.
- **S8 Rules mining** — HDBSCAN→counts→LLM writeup job + endpoint; consent-gated (reuse rules store).
  *Accept:* recurring planted convention surfaces with evidence + ≥3 count.
- **S9 Bi-temporal validity + grey-band LLM adjudication** — edge validity columns; contradiction
  invalidation; LLM tier on 0.55–0.85. *Accept:* superseded decision excluded from point-in-time
  context; borderline pair adjudicated.
- **S10 Leiden communities** (stretch) — optional extra; lazy top-level summaries; global-question
  path. *Accept:* thematic "what has the team worked on" answered from summaries.

Realistic target for this run: **S1–S6 complete & measured** (the moat + explicit asks + correlation),
S7–S10 as time/stability allow. Each slice independently valuable and committed.

## 5. Security & guardrails (non-negotiable)
- **Local-first / offline:** all new compute local; heavy deps lazy-imported optional extras; degrade
  gracefully when absent. No new network calls in the hot path; no telemetry.
- **No secrets to client; provider keys server-side only** (existing invariant). Overrides never carry
  secrets (existing `get_llm` rule).
- **Visibility enforced** on every new read path (company/team/private) exactly as existing store does.
- **Redaction before any upload** stays upstream of all of this (we operate on already-ingested data).
- **Scope:** touch only `apps/api` (+ minimal desktop wiring if a slice needs UI); never modify files
  outside this project; no changes to `~/.claude`, agents, skills.
- Synthetic eval corpus only — no real user data committed.

## 6. Success criteria (what makes this sellable)
- **Moat proof:** a committed, reproducible benchmark showing GraphRAG (3-arm + rerank) beats vanilla
  vector+FTS on cross-session Recall@10 / Hit@3 — the headline for OSS launch + the demo.
- **Explicit asks delivered:** spaCy NER entity extraction; entities connected across sessions;
  right-session retrieval; ≥2 correlation signals beyond the graph (kNN + PPMI).
- **OSS-clean:** light default install, optional extras, full offline path, all tests green
  (≥267 + new), no secret/telemetry regressions.
- **Differentiated:** features no competitor combines — passive ingest + conversation-corpus GraphRAG
  + vendor-neutral + cross-dev rules.

## 7. Execution model
Sequential ralph-loop driven directly (slices share files; parallel agents would conflict). Per slice:
implement → tests → `pytest` green → commit. Keep-alive via caffeinate (running) + ScheduleWakeup
heartbeat. Update `cross-session-graph-memory` memory at the end. No PR/push unless asked (no remote).
