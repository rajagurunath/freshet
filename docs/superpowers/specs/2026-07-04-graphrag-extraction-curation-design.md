# GraphRAG Extraction Overhaul + Graph Curation — Design

**Date:** 2026-07-04
**Status:** Approved (brainstorm with user)
**Predecessors:** `docs/superpowers/plans/2026-06-27-graphrag-cross-session-memory.md` (experiment + benchmark), `2026-06-27-graphrag-RESULTS.md`

## Problem

The production knowledge graph is unusable. Inspection of the real corpus
(`apps/api/data-real/graph.db`, 313 sessions, 123 nodes) shows:

1. **Only NER-produced kinds exist** (`tool`/`service`/`repo`) — zero
   `feature`/`decision`/`person` nodes. The offline build path
   (`graph/build.py` → `extract_ner_graph(use_spacy=False)`) is regex+gazetteer
   only; the LLM extractor never ran on any session (jobs.db has no
   `graph_extract` jobs). The 2026-06-27 experiment already proved NER-only
   gives ~no retrieval lift: feature-level concepts drive cross-session linking.
2. **Regex garbage nodes.** `_RE_SERVICE` matches URL slugs and hyphenated
   prose: `turn-your-api`, `get-started-with-caas-api`, `contracts-and-api`,
   `int-826-io-net-cloud-api`, `pure-server`, `x-api`. `_RE_REPO` captured
   `github.com/login/device` as repo `login/device`.
3. **Meaningless edges.** 901 of 902 edges are `co_occurs` in a star topology
   whose hub is whichever entity the regex found first.
4. **Mega-hubs.** `github` (136 sessions), `s3` (89), `python` (80) connect
   everything to everything, drowning both the viz and graph-arm retrieval.
   Only 1 `same_as` edge exists — entity resolution has nothing real to link.

Additionally there is no way for a user to fix the graph: `GraphPage` and
`SessionGraph` are view-only; no mutation endpoints exist.

## Goals

- Retrieval-first: concepts and decisions exist in the graph so the graph arm
  surfaces the right past sessions; the viz becomes sensible as a byproduct.
- Bounded cost: LLM extraction only on sessions worth it (tiered), suitable
  for OSS users on the default `claude-cli` provider.
- Human curation: view/edit/rename/delete/add nodes and edges; the application
  adapts to edits immediately and edits survive re-extraction.

## Decisions (user-confirmed)

- **Approach:** fix the existing pipeline (hybrid tiered NER+LLM extraction +
  graph hygiene), borrowing session-fact relation semantics — no schema
  redesign.
- **Cost model:** tiered — cheap NER everywhere, LLM extraction only on
  "worthy" sessions via the existing job queue.
- **Rename collision = hard merge + alias memory.** Human intent is trusted:
  edges and provenance move to the survivor, the old name becomes an alias.
  Machine entity-resolution stays soft (`same_as`), per the Slice-1 decision.

## Design

### 1. Extraction quality

**NER precision fixes (`graph/ner.py`):**
- Reject `service` candidates whose only occurrences are inside URLs/paths
  (kills the URL-slug artifacts). A candidate seen once, only in prose,
  requires a second occurrence or code context (backticks / path context) to
  be emitted.
- `repo` extraction blocklists non-repo GitHub routes
  (`login/*`, `orgs/*`, `settings/*`, `search/*`, ...).
- Regression tests use the literal garbage strings observed in `data-real`.

**Tiered LLM concept extraction:**
- A per-session worthiness score — message count, user-prompt volume,
  recency — selects sessions for the existing `graph_extract` job
  (NER → LLM → `entity_resolve` chain in `jobs/handlers.py`). Threshold
  configurable in `config.py`; default targets roughly the top third of the
  corpus (~2.3k local transcripts on the reference machine).
- Better LLM input: replace the current "first 300 chars of the first user
  message" summary (`build.py:to_session`) with title + first user message +
  final assistant message, preferring a real LLM summary when one exists.
- New extraction prompt (`graph/extract.py`): extract `feature` / `decision` /
  `problem` concepts and emit a **controlled relation vocabulary** —
  `worked_on`, `decided`, `fixed`, `uses`, `depends_on` — instead of free-form
  verbs. Relations remain entity↔entity with session provenance via
  `node_sessions`; no schema change. `ALLOWED_KINDS` gains `problem`.

### 2. Graph hygiene

- **Entity document frequency:** a computed `generic` flag on nodes for
  entities appearing in more than ~25% of sessions (threshold in config).
  Generic nodes are hidden by default in the viz (toggle to show) and are
  never used as retrieval seeds or expansion hops (extends the hub guard in
  `graph/retrieve.py`). They remain in the DB for entity-filter queries
  ("which sessions used Redis").
- **PPMI edges replace the star:** `build.py` and `extract_ner_graph` stop
  writing arbitrary-hub `co_occurs` stars; `graph/correlate.py`'s PPMI
  co-occurrence decides which entity pairs get edges. Existing star edges are
  dropped in the rebuild.

### 3. Curation API

New endpoints (thin over `GraphStore`):
- `PATCH /v1/graph/nodes/{id}` — rename / change kind / edit summary. Rename
  onto an existing `(kind, name)` performs a **hard merge**: edges and
  `node_sessions` provenance move to the survivor, the loser is deleted, the
  old name is recorded as an alias of the survivor.
- `DELETE /v1/graph/nodes/{id}`, `DELETE /v1/graph/edges/{id}` — remove with
  tombstone.
- `POST /v1/graph/nodes`, `POST /v1/graph/edges` — manual additions, marked
  `source=human`.

**Curation memory (durability):** a new `curation` table in graph.db records
every human action (aliases, tombstones, field edits, manual adds). Enforced
at upsert time in `GraphStore`:
- aliased names remap to their canonical node automatically on any future
  extraction;
- tombstoned `(kind, name)` pairs are never re-created by machine extraction;
- human-edited fields are never overwritten by machine extraction.

Human edits always win over re-extraction; without this, the next
"Build all graphs" run resurrects every deleted garbage node.

### 4. UI + adaptation

- **GraphPage** becomes editable: selecting a node opens a side panel with
  rename, change-kind, edit-summary, delete, and merge-into (autocomplete over
  existing nodes); selecting an edge allows change-rel / delete; add-node and
  add-edge actions exist on the page. **SessionGraph** (session detail) gets
  the same panel scoped to that session's subgraph.
- **Adaptation:** on rename/merge the store triggers a re-index of the
  affected node — node embedding refreshed (`node_vectors`), `same_as` links
  re-evaluated — so retrieval sees the new name immediately. Retrieval reads
  names/edges live from graph.db, so no other invalidation is needed.

### 5. Rebuild & rollout

Wipe `data-real/graph.db` (current content is noise; the curation table is new
and empty, so nothing human-made is lost). Re-run the offline NER build with
the fixed extractor (minutes), then enqueue tiered LLM jobs (background, uses
the existing progress bar).

Ship order (each slice lands with tests):
1. NER precision fixes
2. Hygiene (generic flag + PPMI edges)
3. Tiered LLM extraction (worthiness + input + prompt/relations)
4. Curation API + curation memory
5. Desktop UI (GraphPage / SessionGraph editing)

The eval harness (`contexthub.eval`) re-runs after slices 1–3 to confirm
retrieval lift on the synthetic corpus (baseline: Hit@3 .85→.90, Bridge
Recall@10 .75→1.0 from the 2026-06-27 experiment).

## Error handling

- All extraction remains best-effort (never blocks the job queue), matching
  the existing contract in `extract.py`.
- Merge is transactional in SQLite; a failed merge leaves both nodes intact.
- Curation endpoints validate ids and return 404/409 with clear messages
  (409 on tombstone-conflicting manual add).

## Testing

- `test_ner.py`: regression cases from real garbage strings; URL-slug
  rejection; repo-route blocklist.
- `test_extract.py`: controlled relation vocabulary; kind `problem`;
  improved-input construction.
- `test_store.py` / new `test_curation.py`: alias remap on upsert, tombstone
  suppression, human-field protection, hard merge (edges + provenance moved,
  transactionality).
- `test_retrieve.py`: generic nodes excluded from seeds/hops.
- Desktop vitest: edit panel actions against a mocked API.
- Eval harness run recorded in the plan after slices 1–3.
