# GraphRAG Extraction Overhaul + Graph Curation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the knowledge graph trustworthy (concept-level entities, no regex garbage, meaningful edges) and human-curatable (rename/merge/delete/add survive re-extraction).

**Architecture:** Fix the existing NER→LLM→resolve pipeline rather than redesign: precision-fix the regex NER, flag generic hub entities by document frequency, replace arbitrary star `co_occurs` edges with corpus-level PPMI edges, add a tiered LLM concept-extraction phase to the offline build, and add a curation layer (aliases/tombstones/human-edit protection) enforced inside `GraphStore.upsert_*` so human edits always beat machine re-extraction.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (graph.db) in `apps/api`; React + TypeScript + vitest in `apps/desktop`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-04-graphrag-extraction-curation-design.md`

## Global Constraints

- No new required Python or npm dependencies.
- All machine extraction is best-effort: it must never raise out of a job/build loop (existing contract in `extract.py` / `ner.py`).
- Node identity is `(kind, normalized-name)`; names normalize via `normalize_name` (lowercase + trim). Every write keeps `session_id` provenance.
- Human curation always wins over machine extraction (aliases remap, tombstones block, human-edited fields are never machine-overwritten).
- Controlled edge vocabulary from the LLM: `worked_on | decided | fixed | uses | depends_on | related_to`.
- API tests: `cd apps/api && . .venv/bin/activate && python -m pytest tests/<file> -v`. Desktop tests: `cd apps/desktop && npx vitest run`.
- Commit after every task. NEVER add a `Co-Authored-By` trailer (user rule).
- API runs on `:8787` (`make api`); test API keys look like `API_KEYS="alice-key:alice:team-red"` → header `Authorization: Bearer alice-key`.

---

### Task 1: NER precision — kill URL-slug services, header prefixes, and non-repo GitHub routes

The real graph contains `service` nodes like `turn-your-api`, `get-started-with-caas-api` (URL slugs), `x-api` (from the `x-api-key` header), and `repo` node `login/device` (from `github.com/login/device`). Fix `extract_code_entities` precision.

**Files:**
- Modify: `apps/api/contexthub/graph/ner.py`
- Test: `apps/api/tests/test_ner.py`

**Interfaces:**
- Consumes: existing `_RE_SERVICE`, `_RE_REPO`, `_add`, `extract_code_entities(text, granular=False)`.
- Produces: same public signature `extract_code_entities(text: str, granular: bool = False) -> list[Entity]`; new module-private `_strip_urls(text: str) -> str` and `_service_candidates(text: str) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/tests/test_ner.py`:

```python
# ---------------------------------------------------------------------------
# Precision regressions — literal garbage observed in the real data-real graph
# ---------------------------------------------------------------------------

class TestServicePrecision:
    def test_service_ignores_url_slugs(self):
        from contexthub.graph.ner import extract_code_entities
        text = (
            "See https://docs.io.net/get-started-with-caas-api for setup, "
            "then read /reference/turn-your-api and "
            "[the guide](https://docs.io.net/contracts-and-api)."
        )
        names = {e.name for e in extract_code_entities(text) if e.kind == "service"}
        assert "get-started-with-caas-api" not in names
        assert "turn-your-api" not in names
        assert "contracts-and-api" not in names

    def test_service_requires_repetition_or_code_context(self):
        from contexthub.graph.ner import extract_code_entities
        # once, prose only → dropped
        once = "Maybe we should split out a billing-service later."
        assert not [e for e in extract_code_entities(once) if e.kind == "service"]
        # once, in backticks → kept
        code = "Deploy `auth-gateway` before the migration."
        names = {e.name for e in extract_code_entities(code) if e.kind == "service"}
        assert "auth-gateway" in names
        # twice in prose → kept
        twice = "The payments-api timed out. Restarting payments-api fixed it."
        names = {e.name for e in extract_code_entities(twice) if e.kind == "service"}
        assert "payments-api" in names

    def test_service_rejects_header_prefixes(self):
        from contexthub.graph.ner import extract_code_entities
        text = "Send the x-api-key header. The x-api-key value rotates daily."
        assert not [e for e in extract_code_entities(text) if e.kind == "service"]


class TestRepoPrecision:
    def test_repo_blocklists_non_repo_github_routes(self):
        from contexthub.graph.ner import extract_code_entities
        text = (
            "Log in at https://github.com/login/device then star "
            "https://github.com/rajagurunath/context-hub — also see "
            "https://github.com/orgs/acme/repositories."
        )
        names = {e.name for e in extract_code_entities(text) if e.kind == "repo"}
        assert "login/device" not in names
        assert "orgs/acme" not in names
        assert "rajagurunath/context-hub" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/api && . .venv/bin/activate && python -m pytest tests/test_ner.py -v -k "Precision"`
Expected: FAIL — `get-started-with-caas-api` and `login/device` are currently extracted; single-mention prose services are currently kept.

- [ ] **Step 3: Implement in `ner.py`**

Add below `_RE_ERROR` (near line 85):

```python
# URLs, markdown link targets, and bare paths are where slug garbage comes from
# ("/get-started-with-caas-api" is a docs anchor, not a service).
_RE_URL = re.compile(r"(?:https?://|www\.)\S+|\]\([^)]*\)", re.IGNORECASE)

# GitHub/GitLab path prefixes that are site routes, not repositories.
_REPO_ROUTE_BLOCKLIST = {
    "login", "orgs", "settings", "search", "topics", "features", "blog",
    "about", "pulls", "issues", "notifications", "marketplace", "sponsors",
    "apps", "repos", "collections", "trending", "explore", "site", "contact",
}


def _strip_urls(text: str) -> str:
    """Blank out URLs / markdown link targets so slug fragments can't match."""
    return _RE_URL.sub(" ", text)


def _service_candidates(text: str) -> list[str]:
    """High-precision service names: not path segments, not header prefixes,
    and (unless code-quoted) mentioned more than once.

    A hyphenated token ending in api/server/gateway/… matched exactly once in
    plain prose is overwhelmingly a slug or a hypothetical, not a service the
    session actually touched (verified on the real corpus: turn-your-api,
    contracts-and-api, x-api). Requiring a second mention or backtick context
    keeps the real ones.
    """
    stripped = _strip_urls(text)
    raw: list[str] = []
    for m in _RE_SERVICE.finditer(stripped):
        start, end = m.start(1), m.end(1)
        before = stripped[start - 1] if start > 0 else " "
        after = stripped[end] if end < len(stripped) else " "
        if before == "/" or after in "/-":
            # path segment ("/reference/turn-your-api") or a longer hyphenated
            # token we only prefix-matched ("x-api-key")
            continue
        raw.append(m.group(1))

    counts: dict[str, int] = {}
    for name in raw:
        counts[name.lower()] = counts.get(name.lower(), 0) + 1

    low = text.lower()
    kept: list[str] = []
    for name in dict.fromkeys(n.lower() for n in raw):
        if counts[name] >= 2 or f"`{name}" in low:
            kept.append(name)
    return kept
```

In `extract_code_entities`, replace the service loop:

```python
    for m in _RE_SERVICE.finditer(text):
        _add(out, "service", m.group(1))
```

with:

```python
    for name in _service_candidates(text):
        _add(out, "service", name)
```

and in the repo loop, add the route blocklist check after the extension check:

```python
    for m in _RE_REPO.finditer(text):
        cand = m.group(1)
        if "." in cand.split("/")[-1]:  # trailing segment has an extension → file
            continue
        if cand.split("/")[0].lower() in _REPO_ROUTE_BLOCKLIST:
            continue  # github.com/login/device, /orgs/…, /settings/… are routes
        _add(out, "repo", cand)
```

- [ ] **Step 4: Run the new tests, then the whole NER file**

Run: `python -m pytest tests/test_ner.py -v`
Expected: the new `Precision` tests PASS. Some existing service-extraction tests may now fail **because their fixtures mention a service exactly once in prose** — that is the intended behavior change. Fix those fixtures by mentioning the service twice or backticking it (do NOT weaken the new rule). Then all of `test_ner.py` passes.

- [ ] **Step 5: Run the full API suite and commit**

Run: `python -m pytest -q`
Expected: all pass (fix any other fixture that relied on single-mention prose services the same way).

```bash
git add apps/api/contexthub/graph/ner.py apps/api/tests/test_ner.py
git commit -m "fix(graph): NER precision — reject URL slugs, header prefixes, non-repo routes"
```

---

### Task 2: Generic-hub flag — document-frequency marking in the store + API shape

`github` (136 sessions), `s3` (89), `python` (80) connect everything. Flag high-DF nodes as `generic` so retrieval and the viz can demote them.

**Files:**
- Modify: `apps/api/contexthub/graph/store.py`
- Modify: `apps/api/contexthub/models.py` (GraphNode)
- Modify: `apps/api/contexthub/api/routes.py` (`_build_graph_response`)
- Modify: `apps/api/contexthub/config.py`
- Test: `apps/api/tests/test_graph.py`

**Interfaces:**
- Produces: `GraphStore.recompute_generic_flags(fraction: float = 0.25, min_total: int = 20) -> int` (returns #nodes flagged); node dicts from `_row_to_node` gain `"generic": bool`; `models.GraphNode.generic: bool = False`; settings `graph_generic_fraction: float`, `graph_generic_min_sessions: int`.

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_graph.py` inside `class TestGraphStore`:

```python
    def test_recompute_generic_flags(self):
        # 30 sessions all mention "github"; "checkout" appears in 2.
        for i in range(30):
            self.store.upsert_node(kind="tool", name="github", session_id=f"s{i}")
        self.store.upsert_node(kind="feature", name="checkout", session_id="s1")
        self.store.upsert_node(kind="feature", name="checkout", session_id="s2")

        flagged = self.store.recompute_generic_flags(fraction=0.25, min_total=20)
        assert flagged == 1
        nodes = {n["name"]: n for n in self.store.list_nodes()}
        assert nodes["github"]["generic"] is True
        assert nodes["checkout"]["generic"] is False

    def test_generic_flags_noop_on_tiny_corpus(self):
        self.store.upsert_node(kind="tool", name="github", session_id="s1")
        self.store.upsert_node(kind="tool", name="github", session_id="s2")
        assert self.store.recompute_generic_flags(fraction=0.25, min_total=20) == 0
        assert self.store.list_nodes()[0]["generic"] is False
```

(`self.store` is how existing `TestGraphStore` tests reference the store — match the surrounding setup.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_graph.py -v -k generic`
Expected: FAIL — `recompute_generic_flags` does not exist / `generic` key missing.

- [ ] **Step 3: Implement**

`store.py` — add a column-migration helper and call it in `_init_db`:

```python
def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
```

In `_init_db`, after the index creation loop:

```python
            _ensure_column(conn, "nodes", "generic", "INTEGER NOT NULL DEFAULT 0")
```

In `_row_to_node`, add:

```python
            "generic": bool(row["generic"]) if "generic" in row.keys() else False,
```

Add the method (after `session_ids_with_nodes`):

```python
    def recompute_generic_flags(self, fraction: float = 0.25, min_total: int = 20) -> int:
        """Flag nodes appearing in more than ``fraction`` of all sessions as generic.

        Generic hubs (a ubiquitous tool like "github") drown the viz and the
        retrieval walk. Below ``min_total`` distinct sessions the corpus is too
        small to judge, so all flags are cleared and nothing is marked.
        Returns the number of nodes flagged.
        """
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM node_sessions"
            ).fetchone()[0]
            conn.execute("UPDATE nodes SET generic = 0")
            if total < min_total:
                conn.commit()
                return 0
            cutoff = max(int(total * fraction), 3)
            rows = conn.execute(
                "SELECT node_id, COUNT(DISTINCT session_id) AS c "
                "FROM node_sessions GROUP BY node_id HAVING c > ?",
                (cutoff,),
            ).fetchall()
            ids = [r["node_id"] for r in rows]
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                conn.execute(
                    f"UPDATE nodes SET generic = 1 WHERE id IN ({','.join('?' * len(chunk))})",
                    chunk,
                )
            conn.commit()
        return len(ids)
```

`models.py` — add to `GraphNode`:

```python
    generic: bool = False
```

`routes.py` — in `_build_graph_response`, add to the `GraphNode(...)` construction:

```python
            generic=bool(n.get("generic", False)),
```

`config.py` — add next to the NER settings block:

```python
    # ---------------------------------------------------------------------------
    # GraphRAG extraction overhaul
    # ---------------------------------------------------------------------------
    # A node appearing in more than this fraction of all sessions is a generic
    # hub: hidden by default in the viz, never a retrieval seed/expansion hop.
    graph_generic_fraction: float = 0.25
    # Corpus floor (distinct sessions) before any node may be flagged generic.
    graph_generic_min_sessions: int = 20
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_graph.py -v`
Expected: PASS (including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add apps/api/contexthub/graph/store.py apps/api/contexthub/models.py apps/api/contexthub/api/routes.py apps/api/contexthub/config.py apps/api/tests/test_graph.py
git commit -m "feat(graph): generic-hub flag via per-entity document frequency"
```

---

### Task 3: Retrieval — generic nodes never seed or propagate the graph walk

**Files:**
- Modify: `apps/api/contexthub/graph/retrieve.py` (`graph_search`)
- Test: `apps/api/tests/test_retrieve.py`

**Interfaces:**
- Consumes: `store.find_nodes_by_terms` / `store.get_nodes` node dicts now carrying `generic` (Task 2).
- Produces: unchanged `graph_search(...)` signature; generic nodes are excluded from seeds and from expansion (they may still be scored when reached).

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_retrieve.py` (uses `GraphStore` directly like the file's other tests):

```python
def test_generic_nodes_do_not_seed_the_walk(tmp_path):
    from contexthub.graph.retrieve import graph_search
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    for i in range(30):
        store.upsert_node(kind="tool", name="python", session_id=f"s{i}")
    store.upsert_node(kind="feature", name="checkout", session_id="s1")
    store.recompute_generic_flags(fraction=0.25, min_total=20)

    # "python" is the only term match but it is generic → no seeds → no results.
    assert graph_search("python performance tips", store) == []
    # a non-generic concept still seeds normally
    assert "s1" in graph_search("checkout flow", store)


def test_generic_seed_sessions_do_not_flood_via_hub(tmp_path):
    from contexthub.graph.retrieve import graph_search
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    for i in range(30):
        store.upsert_node(kind="tool", name="python", session_id=f"s{i}")
    a = store.upsert_node(kind="feature", name="checkout", session_id="seed-sess")
    hub = store.upsert_node(kind="tool", name="python", session_id="seed-sess")
    store.upsert_edge(src=a, dst=hub, rel="uses", session_id="seed-sess")
    store.recompute_generic_flags(fraction=0.25, min_total=20)

    ranked = graph_search("unrelated words", store, seed_session_ids=["seed-sess"])
    # seed-sess surfaces via its non-generic entity; the 30 hub-only sessions must not.
    assert "s5" not in ranked
    assert "seed-sess" in ranked
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_retrieve.py -v -k generic`
Expected: FAIL — currently `python performance tips` seeds from the generic node and ranks sessions.

- [ ] **Step 3: Implement in `retrieve.py`**

In `graph_search`, replace the seed-collection block (section `--- 1.`) with:

```python
    seeds: dict[str, float] = {}

    term_list = list(terms) if terms is not None else question_terms(question)
    if term_list:
        try:
            for n in store.find_nodes_by_terms(
                term_list, caller_user_id=caller_user_id,
                caller_team=caller_team, limit=30,
            ):
                if n.get("generic"):
                    continue  # a ubiquitous entity says nothing about the query
                seeds[n["id"]] = max(seeds.get(n["id"], 0.0), 1.0)
        except Exception:
            pass

    sess_seed_ids: list[str] = []
    for sid in seed_session_ids or []:
        try:
            sess_seed_ids.extend(store.node_ids_for_session(sid))
        except Exception:
            continue
    if sess_seed_ids:
        try:
            for n in store.get_nodes(sess_seed_ids):
                if not n.get("generic"):
                    seeds[n["id"]] = max(seeds.get(n["id"], 0.0), 1.0)
        except Exception:
            for nid in sess_seed_ids:
                seeds[nid] = max(seeds.get(nid, 0.0), 1.0)

    for nid in extra_seed_node_ids or []:
        seeds[nid] = max(seeds.get(nid, 0.0), 1.0)
```

In the BFS loop (section `--- 2.`), fetch the frontier rows once per hop and skip expansion through generic nodes. Right after `next_frontier: dict[str, float] = {}`:

```python
        try:
            frontier_rows = {r["id"]: r for r in store.get_nodes(list(frontier))}
        except Exception:
            frontier_rows = {}
```

and immediately before the existing hub guard (`if len(store.sessions_for_node(nid)) > hub_max_sessions`):

```python
            row = frontier_rows.get(nid)
            if row is not None and row.get("generic"):
                continue  # score generic nodes, never expand through them
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_retrieve.py tests/test_graph_augment.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/contexthub/graph/retrieve.py apps/api/tests/test_retrieve.py
git commit -m "feat(graph): exclude generic hub nodes from retrieval seeds and expansion"
```

---

### Task 4: PPMI co-occurrence edges replace the arbitrary star

901/902 edges today are `co_occurs` stars around whichever entity the regex found first. Stop writing them; derive corpus-level PPMI edges instead.

**Files:**
- Modify: `apps/api/contexthub/graph/correlate.py` (new `refresh_cooccur_edges`)
- Modify: `apps/api/contexthub/graph/store.py` (new `delete_edges_by_rel`)
- Modify: `apps/api/contexthub/graph/ner.py` (`extract_ner_graph` stops writing star edges)
- Modify: `apps/api/contexthub/graph/build.py` (worker calls refresh + generic recompute at end)
- Test: `apps/api/tests/test_correlate.py`, `apps/api/tests/test_ner.py`

**Interfaces:**
- Produces: `refresh_cooccur_edges(store, min_cooccur: int = 2, max_pairs: int = 500) -> int` (edges written); `GraphStore.delete_edges_by_rel(rel: str) -> int` (rows deleted). `extract_ner_graph` return shape unchanged but `edges_upserted` is now always 0.

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/tests/test_correlate.py`:

```python
def test_refresh_cooccur_edges_replaces_star_with_ppmi(tmp_path):
    from contexthub.graph.correlate import refresh_cooccur_edges
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    # redis+celery always together (3/6 sessions); postgres everywhere (6/6).
    for i in range(3):
        store.upsert_node(kind="tool", name="redis", session_id=f"pair{i}")
        store.upsert_node(kind="tool", name="celery", session_id=f"pair{i}")
    for i in range(6):
        sid = f"pair{i}" if i < 3 else f"solo{i}"
        store.upsert_node(kind="tool", name="postgres", session_id=sid)
    # a stale star edge that must be replaced
    nodes = {n["name"]: n["id"] for n in store.list_nodes()}
    store.upsert_edge(src=nodes["postgres"], dst=nodes["redis"], rel="co_occurs")

    written = refresh_cooccur_edges(store, min_cooccur=2)
    assert written >= 1
    edges = store.list_edges()
    cooc = [e for e in edges if e["rel"] == "co_occurs"]
    pairs = {frozenset((e["src"], e["dst"])) for e in cooc}
    # redis↔celery survives (high PPMI); the stale postgres↔redis star is gone
    assert frozenset((nodes["redis"], nodes["celery"])) in pairs
    assert frozenset((nodes["postgres"], nodes["redis"])) not in pairs


def test_delete_edges_by_rel(tmp_path):
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    a = store.upsert_node(kind="tool", name="a", session_id="s1")
    b = store.upsert_node(kind="tool", name="b", session_id="s1")
    store.upsert_edge(src=a, dst=b, rel="co_occurs")
    store.upsert_edge(src=a, dst=b, rel="uses")
    assert store.delete_edges_by_rel("co_occurs") == 1
    assert [e["rel"] for e in store.list_edges()] == ["uses"]
```

And in `apps/api/tests/test_ner.py`, add:

```python
def test_extract_ner_graph_writes_no_star_edges(tmp_path):
    from contexthub.graph.ner import extract_ner_graph
    from contexthub.graph.store import GraphStore
    from contexthub.models import Message, NormalizedSession

    store = GraphStore(str(tmp_path / "g.db"))
    sess = NormalizedSession(
        id="s1", tool="claude-code", title="t",
        messages=[Message(id="m1", role="user",
                          text="We wired `payments-api` to redis. payments-api works.")],
    )
    res = extract_ner_graph(sess, None, store)
    assert res["nodes_upserted"] >= 2
    assert res["edges_upserted"] == 0
    assert store.list_edges() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_correlate.py tests/test_ner.py -v -k "cooccur or star or delete_edges"`
Expected: FAIL — `refresh_cooccur_edges` / `delete_edges_by_rel` don't exist; star edges are written.

- [ ] **Step 3: Implement**

`store.py` — add after `edges_by_rel`:

```python
    def delete_edges_by_rel(self, rel: str) -> int:
        """Delete every edge with the given relation. Returns rows deleted."""
        rel = (rel or "").strip().lower()
        if not rel:
            return 0
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM edges WHERE LOWER(rel) = ?", (rel,))
            conn.commit()
            return cur.rowcount
```

`correlate.py` — add at the end:

```python
def refresh_cooccur_edges(
    store: Any,
    min_cooccur: int = 2,
    max_pairs: int = 500,
) -> int:
    """Rebuild ``co_occurs`` edges from corpus-level PPMI.

    Replaces the old per-session star topology (hub = first-matched entity,
    semantically void) with edges only between statistically associated pairs.
    Idempotent: deletes all existing ``co_occurs`` edges first. PPMI itself
    down-weights ubiquitous entities, so generic hubs rarely earn an edge.
    Returns the number of edges written.
    """
    pairs = ppmi_entity_pairs(store, min_cooccur=min_cooccur)
    nodes = store.list_nodes(limit=100_000)
    by_key = {f"{n['kind']}:{n['name']}": n["id"] for n in nodes}
    store.delete_edges_by_rel("co_occurs")
    written = 0
    for p in pairs[:max_pairs]:
        a, b = by_key.get(p.a), by_key.get(p.b)
        if not a or not b:
            continue
        try:
            store.upsert_edge(src=a, dst=b, rel="co_occurs", weight=round(p.ppmi, 4))
            written += 1
        except ValueError:
            continue
    return written
```

`ner.py` — in `extract_ner_graph`, delete the whole star-edge block:

```python
        # Connect entities with a STAR to the first node rather than all-pairs: …
        edges = 0
        if node_ids:
            hub = node_ids[0]
            for nid in node_ids[1:]:
                …
        return {"nodes_upserted": len(node_ids), "edges_upserted": edges}
```

replace with:

```python
        # Edges are NOT written per session: the old star topology (hub = first
        # matched entity) was semantically void. Corpus-level PPMI pairs are
        # rebuilt by contexthub.graph.correlate.refresh_cooccur_edges after a
        # build/backfill pass instead.
        return {"nodes_upserted": len(node_ids), "edges_upserted": 0}
```

and update the function docstring (it mentions co_occurs edges).

`build.py` — at the end of `_worker`, before the final progress update:

```python
    # Corpus-level post-passes: statistically meaningful co-occurrence edges and
    # generic-hub flags (both idempotent, both cheap relative to the build).
    try:
        from contexthub.config import get_settings
        from contexthub.graph.correlate import refresh_cooccur_edges

        settings = get_settings()
        refresh_cooccur_edges(store)
        store.recompute_generic_flags(
            fraction=getattr(settings, "graph_generic_fraction", 0.25),
            min_total=getattr(settings, "graph_generic_min_sessions", 20),
        )
    except Exception:
        pass
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_correlate.py tests/test_ner.py tests/test_graph.py -v`
Expected: PASS. Any existing test asserting `edges_upserted > 0` from `extract_ner_graph` must be updated to assert `== 0` (intended behavior change).

- [ ] **Step 5: Commit**

```bash
git add apps/api/contexthub/graph/correlate.py apps/api/contexthub/graph/store.py apps/api/contexthub/graph/ner.py apps/api/contexthub/graph/build.py apps/api/tests/test_correlate.py apps/api/tests/test_ner.py
git commit -m "feat(graph): PPMI co-occurrence edges replace per-session star topology"
```

---

### Task 5: LLM extractor — concept kinds + controlled relation vocabulary

**Files:**
- Modify: `apps/api/contexthub/graph/extract.py`
- Test: `apps/api/tests/test_graph.py`

**Interfaces:**
- Produces: `ALLOWED_KINDS` gains `"problem"`; new module constant `ALLOWED_RELS = {"worked_on", "decided", "fixed", "uses", "depends_on", "related_to"}`; unknown rels coerce to `"related_to"`. `extract_graph` signature unchanged.

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_graph.py` (near the existing mocked-LLM extract tests — reuse their stub-LLM pattern; the stub is a small class with `available() -> True` and `complete(system, user, max_tokens) -> str`):

```python
def test_extract_accepts_problem_kind_and_coerces_unknown_rels(tmp_path):
    import json as _json

    from contexthub.graph.extract import extract_graph
    from contexthub.graph.store import GraphStore
    from contexthub.models import NormalizedSession

    class StubLLM:
        def available(self):
            return True

        def complete(self, system, user, max_tokens=1024):
            return _json.dumps({
                "nodes": [
                    {"kind": "problem", "name": "session id mismatch",
                     "summary": "ids diverged between stores"},
                    {"kind": "feature", "name": "checkout", "summary": "checkout flow"},
                ],
                "edges": [
                    {"src": "checkout", "dst": "session id mismatch",
                     "rel": "was blocked by"},
                ],
            })

    store = GraphStore(str(tmp_path / "g.db"))
    sess = NormalizedSession(id="s1", tool="claude-code", title="t")
    res = extract_graph(sess, "summary text", store, llm=StubLLM())
    assert res["nodes_upserted"] == 2
    kinds = {n["kind"] for n in store.list_nodes()}
    assert "problem" in kinds  # not bucketed into "feature"
    edges = store.list_edges()
    assert len(edges) == 1
    assert edges[0]["rel"] == "related_to"  # unknown verb coerced, not dropped
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_graph.py -v -k coerces`
Expected: FAIL — `problem` becomes `feature`; rel stays `was blocked by`.

- [ ] **Step 3: Implement in `extract.py`**

Update the constants:

```python
# Allowed node kinds: structural (repo|service|tool|pr|person) + the concept
# kinds that drive cross-session linking (feature|decision|problem).
ALLOWED_KINDS = {"repo", "service", "feature", "person", "decision", "tool", "pr", "problem"}

# Controlled relation vocabulary — the UI and retrieval rely on these being
# stable. Unknown verbs from the LLM coerce to "related_to" instead of being
# dropped (the link is still signal even when the label is fuzzy).
ALLOWED_RELS = {"worked_on", "decided", "fixed", "uses", "depends_on", "related_to"}
```

Replace `_SYSTEM_PROMPT` with:

```python
_SYSTEM_PROMPT = """\
You extract a small knowledge graph from a coding-session summary or excerpt.

Return ONLY a JSON object with this exact shape (no prose, no markdown fence):
{
  "nodes": [{"kind": "<one of: repo|service|feature|person|decision|tool|pr|problem>",
             "name": "<short canonical name>",
             "summary": "<one short phrase>"}],
  "edges": [{"src": "<a node name above>", "dst": "<a node name above>",
             "rel": "<one of: worked_on|decided|fixed|uses|depends_on|related_to>"}]
}

Focus on the CONCEPTS a developer would search for later:
- feature: the capability being built or changed (e.g. "checkout", "graph search")
- problem: the bug or issue being investigated (e.g. "session id mismatch")
- decision: a choice that was made (e.g. "sqlite over kuzudb")
Also include the concrete repo/service/tool entities the session actually touched.

Rules:
- Only include entities that clearly appear in the input. Skip generic terms
  ("code", "the api", "a bug", "the user").
- Use the canonical short name (e.g. "checkout", not "the checkout feature").
- rel MUST be one of: worked_on, decided, fixed, uses, depends_on, related_to.
- Edge src/dst MUST be names that appear in the nodes list.
- Keep it small: at most ~10 nodes and ~10 edges.
- If nothing graph-worthy is present, return {"nodes": [], "edges": []}.
"""
```

In the edge loop of `extract_graph`, after `rel = str(e.get("rel", "")).strip()`:

```python
        rel = rel.lower().replace(" ", "_")
        if rel and rel not in ALLOWED_RELS:
            rel = "related_to"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_graph.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/contexthub/graph/extract.py apps/api/tests/test_graph.py
git commit -m "feat(graph): concept-focused extraction prompt + controlled relation vocabulary"
```

---

### Task 6: Tiered LLM concept extraction in the offline build

The offline build (the path that actually built the user's graph) never calls the LLM, and its "summary" is the first 300 chars of the first user message. Add a worthiness-ranked LLM phase with resume tracking.

**Files:**
- Modify: `apps/api/contexthub/graph/build.py`
- Modify: `apps/api/contexthub/graph/store.py` (session_extract tracking table)
- Modify: `apps/api/contexthub/config.py`
- Test: `apps/api/tests/test_build.py` (new)

**Interfaces:**
- Produces: `session_worthiness(parsed: dict) -> float` (0..1), `llm_input_for(parsed: dict) -> str` in `build.py`; `GraphStore.mark_llm_extracted(session_id: str) -> None`, `GraphStore.llm_extracted_session_ids() -> set[str]`; settings `graph_llm_enabled: bool = True`, `graph_llm_fraction: float = 0.33`, `graph_llm_max_sessions: int = 300`.
- Consumes: `extract_graph(session, summary, store, visibility=…)` from Task 5 (the `summary` argument is the preferred LLM input).

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_build.py`:

```python
"""Unit tests for the offline graph build helpers (worthiness + LLM input)."""

import os
import time


def _parsed(path: str, messages: list[tuple[str, str]], tool: str = "claude-code") -> dict:
    return {"messages": messages, "cwd": "/tmp/proj", "ts": None, "tool": tool, "path": path}


def test_session_worthiness_prefers_rich_recent_sessions(tmp_path):
    from contexthub.graph.build import session_worthiness

    rich = tmp_path / "rich.jsonl"
    rich.write_text("x")
    poor = tmp_path / "poor.jsonl"
    poor.write_text("x")
    old = time.time() - 300 * 86400
    os.utime(poor, (old, old))

    rich_parsed = _parsed(str(rich), [("user", "long prompt " * 100), ("assistant", "a" * 500)] * 10)
    poor_parsed = _parsed(str(poor), [("user", "hi")])

    assert session_worthiness(rich_parsed) > session_worthiness(poor_parsed)
    assert 0.0 <= session_worthiness(poor_parsed) <= 1.0
    assert 0.0 <= session_worthiness(rich_parsed) <= 1.0


def test_llm_input_includes_title_ask_and_outcome(tmp_path):
    from contexthub.graph.build import llm_input_for

    p = _parsed(str(tmp_path / "s.jsonl"), [
        ("user", "Fix the checkout race condition\nmore detail here"),
        ("assistant", "intermediate"),
        ("assistant", "Root cause was a stale session id; fixed in store.py"),
    ])
    text = llm_input_for(p)
    assert text.startswith("Title: Fix the checkout race condition")
    assert "checkout race condition" in text
    assert "stale session id" in text  # LAST assistant message, not the first


def test_llm_extract_tracking_roundtrip(tmp_path):
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    assert store.llm_extracted_session_ids() == set()
    store.mark_llm_extracted("s1")
    store.mark_llm_extracted("s1")  # idempotent
    assert store.llm_extracted_session_ids() == {"s1"}
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_build.py -v`
Expected: FAIL — functions/methods don't exist.

- [ ] **Step 3: Implement**

`config.py` — extend the overhaul block from Task 2:

```python
    # Tiered LLM concept extraction in the offline graph build: NER runs on
    # everything; the LLM (feature/decision/problem concepts) only on the
    # worthiest fraction of sessions, capped per run. Resumable via graph.db.
    graph_llm_enabled: bool = True
    graph_llm_fraction: float = 0.33
    graph_llm_max_sessions: int = 300
```

`store.py` — add table + methods. In `_init_db` after `_ensure_column`:

```python
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_extract (
                    session_id TEXT PRIMARY KEY,
                    llm_done   INTEGER NOT NULL DEFAULT 0
                );
                """
            )
```

Methods (after `recompute_generic_flags`):

```python
    def mark_llm_extracted(self, session_id: str) -> None:
        """Record that the LLM concept pass ran for a session (build resume)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO session_extract (session_id, llm_done) VALUES (?, 1)",
                (session_id,),
            )
            conn.commit()

    def llm_extracted_session_ids(self) -> set[str]:
        """Session ids whose LLM concept pass already ran."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id FROM session_extract WHERE llm_done = 1"
            ).fetchall()
        return {r["session_id"] for r in rows}
```

`build.py` — add after `to_session`:

```python
def session_worthiness(parsed: dict) -> float:
    """0..1 score of how much cross-session signal a transcript likely holds.

    Components: conversation depth (message count), how much the *user* wrote
    (prompt volume ≈ intent density), and recency (mtime). Weights are coarse
    on purpose — this ranks sessions for the LLM budget, nothing more.
    """
    msgs = parsed.get("messages") or []
    n = len(msgs)
    user_chars = sum(len(t) for r, t in msgs if r == "user")
    try:
        age_days = max(0.0, (time.time() - os.path.getmtime(parsed["path"])) / 86400.0)
    except OSError:
        age_days = 365.0
    recency = max(0.0, 1.0 - age_days / 180.0)
    return 0.4 * min(n / 20.0, 1.0) + 0.4 * min(user_chars / 4000.0, 1.0) + 0.2 * recency


def llm_input_for(parsed: dict) -> str:
    """Concept-extraction input: title + the ask + the outcome.

    Replaces the old 'first 300 chars of the first user message' — the final
    assistant message is where decisions and root causes get stated.
    """
    msgs = parsed.get("messages") or []
    first_user = next((t for r, t in msgs if r == "user"), "")
    last_assistant = next((t for r, t in reversed(msgs) if r == "assistant"), "")
    title = (first_user.strip().splitlines()[0] if first_user else parsed.get("tool", ""))[:120]
    return (
        f"Title: {title}\n\n"
        f"User request:\n{first_user[:2500]}\n\n"
        f"Final outcome:\n{last_assistant[:2500]}"
    )
```

In `_worker`, after the NER loop and **before** the Task-4 corpus post-passes, add phase 2:

```python
    # Phase 2 — tiered LLM concept extraction on the worthiest sessions.
    # NER (above) gave every session its structural backbone; this pass adds
    # the feature/decision/problem concepts that drive cross-session linking.
    try:
        from contexthub.config import get_settings

        settings = get_settings()
        llm_enabled = bool(getattr(settings, "graph_llm_enabled", True))
    except Exception:
        settings, llm_enabled = None, False

    if llm_enabled:
        from contexthub.graph.extract import extract_graph

        try:
            done_llm = store.llm_extracted_session_ids()
        except Exception:
            done_llm = set()
        scored: list[tuple[float, dict]] = []
        for path, kind in files:
            sid = os.path.splitext(os.path.basename(path))[0]
            if sid in done_llm:
                continue
            parsed = parse_claude(path) if kind == "claude" else parse_codex(path)
            if parsed:
                scored.append((session_worthiness(parsed), parsed))
        scored.sort(key=lambda t: t[0], reverse=True)

        frac = float(getattr(settings, "graph_llm_fraction", 0.33))
        cap = int(getattr(settings, "graph_llm_max_sessions", 300))
        take = min(max(int(len(scored) * frac), 1 if scored else 0), cap)
        worthy = [p for _, p in scored[:take]]

        with _lock:
            _progress["total"] += len(worthy)
        for parsed in worthy:
            try:
                sess, _ = to_session(parsed)
                extract_graph(
                    session=sess, summary=llm_input_for(parsed),
                    store=store, visibility="company",
                )
                store.mark_llm_extracted(sess.id)
            except Exception:
                pass
            finally:
                with _lock:
                    _progress["done"] += 1
```

(`extract_graph` builds its own LLM from settings when `llm=None` — same as the job handler; if the LLM is unavailable it returns zero counts without raising, and the session is still marked done for this run only after the call, so a later run with a working LLM retries only unmarked ones. Move `store.mark_llm_extracted(sess.id)` inside the `try` as shown so LLM-crash sessions stay unmarked.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_build.py tests/test_graph.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/contexthub/graph/build.py apps/api/contexthub/graph/store.py apps/api/contexthub/config.py apps/api/tests/test_build.py
git commit -m "feat(graph): tiered LLM concept extraction in the offline build (worthiness-ranked, resumable)"
```

---

### Task 7: Curation store — aliases, tombstones, hard merge, human-edit protection

The durability core: a `curation` table + enforcement inside `upsert_node`/`upsert_edge`, plus the human operations (`rename_node` with hard merge, `update_node`, `delete_node`, `delete_edge`, `create_node`, `create_edge`).

**Files:**
- Modify: `apps/api/contexthub/graph/store.py`
- Test: `apps/api/tests/test_curation.py` (new)

**Interfaces (all on `GraphStore`):**
- `rename_node(node_id: str, new_name: str) -> dict` → `{"id": <surviving id>, "merged": bool}`; raises `KeyError` (missing), `ValueError` (empty name).
- `update_node(node_id: str, kind: str | None = None, summary: str | None = None) -> None`; raises `KeyError`, `ValueError` (kind change collides with existing (kind,name)).
- `delete_node(node_id: str) -> None` (tombstones (kind,name), removes node+edges+provenance); raises `KeyError`.
- `delete_edge(edge_id: str) -> None` (tombstones (src,dst,rel)); raises `KeyError`.
- `create_node(kind: str, name: str, summary: str | None = None, visibility: str = "company") -> dict` → `{"id", "kind", "name", "summary"}`; raises `ValueError` on tombstoned name.
- `create_edge(src: str, dst: str, rel: str) -> str` → edge id; raises `KeyError` (endpoint missing), `ValueError` (tombstoned edge).
- Enforcement: `upsert_node` raises `ValueError` for tombstoned (kind,name), transparently returns the canonical node id for aliased names (adding provenance), and never overwrites `human_edited` rows' summary/kind/visibility. `upsert_edge` raises `ValueError` for tombstoned (src,dst,rel).

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_curation.py`:

```python
"""Curation memory: human edits always beat machine re-extraction."""

import pytest

from contexthub.graph.store import GraphStore


@pytest.fixture
def store(tmp_path):
    return GraphStore(str(tmp_path / "graph.db"))


def test_deleted_node_is_not_resurrected_by_extraction(store):
    nid = store.upsert_node(kind="service", name="turn-your-api", session_id="s1")
    store.delete_node(nid)
    assert store.list_nodes() == []
    with pytest.raises(ValueError):
        store.upsert_node(kind="service", name="turn-your-api", session_id="s2")
    assert store.list_nodes() == []


def test_rename_creates_alias_that_remaps_future_extractions(store):
    nid = store.upsert_node(kind="feature", name="payment-checkout", session_id="s1")
    res = store.rename_node(nid, "checkout")
    assert res == {"id": nid, "merged": False}
    # machine re-extraction of the OLD name lands on the renamed node
    assert store.upsert_node(kind="feature", name="payment-checkout", session_id="s2") == nid
    assert set(store.sessions_for_node(nid)) == {"s1", "s2"}
    assert [n["name"] for n in store.list_nodes()] == ["checkout"]


def test_rename_onto_existing_node_hard_merges(store):
    a = store.upsert_node(kind="feature", name="payment-checkout", session_id="s1")
    b = store.upsert_node(kind="feature", name="checkout", session_id="s2")
    tool = store.upsert_node(kind="tool", name="stripe", session_id="s1")
    store.upsert_edge(src=a, dst=tool, rel="uses", session_id="s1")
    store.upsert_edge(src=b, dst=tool, rel="uses", session_id="s2")

    res = store.rename_node(a, "checkout")
    assert res == {"id": b, "merged": True}
    assert {n["name"] for n in store.list_nodes()} == {"checkout", "stripe"}
    # provenance moved
    assert set(store.sessions_for_node(b)) == {"s1", "s2"}
    # duplicate edges collapsed, weights accumulated
    uses = [e for e in store.list_edges() if e["rel"] == "uses"]
    assert len(uses) == 1
    assert uses[0]["weight"] == 2.0
    # the old name now aliases to the survivor
    assert store.upsert_node(kind="feature", name="payment-checkout", session_id="s3") == b


def test_human_edited_fields_survive_machine_upsert(store):
    nid = store.upsert_node(kind="feature", name="checkout", session_id="s1", summary="machine")
    store.update_node(nid, summary="the checkout flow (human)")
    store.upsert_node(kind="feature", name="checkout", session_id="s2", summary="machine again")
    node = store.get_nodes([nid])[0]
    assert node["summary"] == "the checkout flow (human)"
    assert set(store.sessions_for_node(nid)) == {"s1", "s2"}  # provenance still accumulates


def test_update_node_kind_collision_raises(store):
    a = store.upsert_node(kind="feature", name="checkout", session_id="s1")
    store.upsert_node(kind="decision", name="checkout", session_id="s2")
    with pytest.raises(ValueError):
        store.update_node(a, kind="decision")


def test_deleted_edge_is_not_rewritten(store):
    a = store.upsert_node(kind="feature", name="checkout", session_id="s1")
    b = store.upsert_node(kind="tool", name="stripe", session_id="s1")
    eid = store.upsert_edge(src=a, dst=b, rel="uses")
    store.delete_edge(eid)
    with pytest.raises(ValueError):
        store.upsert_edge(src=a, dst=b, rel="uses")
    assert store.list_edges() == []


def test_manual_add_of_tombstoned_name_conflicts(store):
    nid = store.upsert_node(kind="tool", name="foo", session_id="s1")
    store.delete_node(nid)
    with pytest.raises(ValueError):
        store.create_node(kind="tool", name="foo")


def test_create_node_and_edge_by_hand(store):
    node = store.create_node(kind="decision", name="sqlite over kuzudb", summary="supply-chain risk")
    other = store.create_node(kind="feature", name="graph store")
    eid = store.create_edge(src=node["id"], dst=other["id"], rel="decided")
    assert eid
    with pytest.raises(KeyError):
        store.create_edge(src=node["id"], dst="missing-id", rel="uses")
    # human-created summary is protected from machine overwrite
    store.upsert_node(kind="decision", name="sqlite over kuzudb", session_id="s9", summary="machine")
    assert store.get_nodes([node["id"]])[0]["summary"] == "supply-chain risk"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_curation.py -v`
Expected: FAIL — none of the methods exist.

- [ ] **Step 3: Implement in `store.py`**

Add `import json` at the top. In `_init_db`, after the `session_extract` table:

```python
            _ensure_column(conn, "nodes", "human_edited", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS curation (
                    id           TEXT PRIMARY KEY,
                    action       TEXT NOT NULL,  -- alias|tombstone_node|tombstone_edge|edit|add
                    kind         TEXT,
                    name         TEXT,
                    canonical_id TEXT,
                    payload      TEXT,
                    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_curation_lookup ON curation(action, kind, name);"
            )
```

Private helpers (place before `upsert_node`):

```python
    # ------------------------------------------------------------------
    # Curation memory (human edits beat machine re-extraction)
    # ------------------------------------------------------------------

    def _curation_insert(
        self,
        conn: sqlite3.Connection,
        action: str,
        kind: Optional[str] = None,
        name: Optional[str] = None,
        canonical_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        conn.execute(
            "INSERT INTO curation (id, action, kind, name, canonical_id, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), action, kind, name, canonical_id,
             json.dumps(payload) if payload else None),
        )

    def _node_tombstoned(self, conn: sqlite3.Connection, kind: str, name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM curation WHERE action = 'tombstone_node' AND kind = ? AND name = ? LIMIT 1",
            (kind, name),
        ).fetchone() is not None

    def _edge_tombstoned(self, conn: sqlite3.Connection, src: str, dst: str, rel: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM curation WHERE action = 'tombstone_edge' AND name = ? LIMIT 1",
            (f"{src}|{dst}|{rel}",),
        ).fetchone() is not None

    def _alias_target(self, conn: sqlite3.Connection, kind: str, name: str) -> Optional[str]:
        row = conn.execute(
            "SELECT canonical_id FROM curation WHERE action = 'alias' AND kind = ? AND name = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (kind, name),
        ).fetchone()
        if row is None:
            return None
        # The canonical node may itself have been merged away or deleted since.
        ok = conn.execute("SELECT 1 FROM nodes WHERE id = ?", (row["canonical_id"],)).fetchone()
        return row["canonical_id"] if ok else None
```

`upsert_node` enforcement — inside the `with self._connect() as conn:` block, **before** the existing SELECT:

```python
            if self._node_tombstoned(conn, kind, norm):
                raise ValueError(f"node ({kind}, {norm}) was removed by the user")
            target = self._alias_target(conn, kind, norm)
            if target:
                if session_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO node_sessions (node_id, session_id) VALUES (?, ?)",
                        (target, session_id),
                    )
                conn.commit()
                return target
```

Human-field protection — change the SELECT to include `human_edited`:

```python
            cur = conn.execute(
                "SELECT id, visibility, summary, human_edited FROM nodes WHERE kind = ? AND name = ?",
                (kind, norm),
            )
```

and wrap the existing else-branch update logic (the visibility/summary block from `cur_vis = …` through the `elif new_summary…` update) in:

```python
                if not row["human_edited"]:
                    <existing visibility/summary update logic, unchanged>
```

(provenance insert below stays outside the guard — human-edited nodes still accumulate sessions).

`upsert_edge` enforcement — inside its `with self._connect() as conn:` block, before the SELECT:

```python
            if self._edge_tombstoned(conn, src, dst, rel):
                raise ValueError("edge was removed by the user")
```

Merge helper + public operations (place after the read methods, before the singleton):

```python
    # ------------------------------------------------------------------
    # Human curation operations
    # ------------------------------------------------------------------

    def _merge_into(self, conn: sqlite3.Connection, loser_id: str, survivor_id: str) -> None:
        """Move edges + provenance from loser to survivor, then drop the loser.

        Runs inside the caller's transaction/connection so rename-with-merge is
        atomic: a failure rolls the whole thing back.
        """
        conn.execute(
            "INSERT OR IGNORE INTO node_sessions (node_id, session_id) "
            "SELECT ?, session_id FROM node_sessions WHERE node_id = ?",
            (survivor_id, loser_id),
        )
        conn.execute("DELETE FROM node_sessions WHERE node_id = ?", (loser_id,))
        for e in conn.execute(
            "SELECT * FROM edges WHERE src = ? OR dst = ?", (loser_id, loser_id)
        ).fetchall():
            new_src = survivor_id if e["src"] == loser_id else e["src"]
            new_dst = survivor_id if e["dst"] == loser_id else e["dst"]
            if new_src == new_dst:
                conn.execute("DELETE FROM edges WHERE id = ?", (e["id"],))
                continue
            dup = conn.execute(
                "SELECT id FROM edges WHERE src = ? AND dst = ? AND rel = ?",
                (new_src, new_dst, e["rel"]),
            ).fetchone()
            if dup:
                conn.execute(
                    "UPDATE edges SET weight = weight + ? WHERE id = ?",
                    (e["weight"], dup["id"]),
                )
                conn.execute("DELETE FROM edges WHERE id = ?", (e["id"],))
            else:
                conn.execute(
                    "UPDATE edges SET src = ?, dst = ? WHERE id = ?",
                    (new_src, new_dst, e["id"]),
                )
        conn.execute("DELETE FROM nodes WHERE id = ?", (loser_id,))

    def rename_node(self, node_id: str, new_name: str) -> dict[str, Any]:
        """Rename a node. Renaming onto an existing (kind, name) hard-merges.

        Either way the old name becomes an alias, so future machine extraction
        of it lands on the surviving node. Returns {"id": survivor, "merged": bool}.
        """
        norm = normalize_name(new_name)
        if not norm:
            raise ValueError("new name is required")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            if row is None:
                raise KeyError(node_id)
            kind, old_name = row["kind"], row["name"]
            if norm == old_name:
                return {"id": node_id, "merged": False}
            existing = conn.execute(
                "SELECT id FROM nodes WHERE kind = ? AND name = ?", (kind, norm)
            ).fetchone()
            if existing and existing["id"] != node_id:
                survivor = existing["id"]
                self._merge_into(conn, loser_id=node_id, survivor_id=survivor)
                self._curation_insert(conn, "alias", kind=kind, name=old_name, canonical_id=survivor)
                conn.execute("UPDATE nodes SET human_edited = 1 WHERE id = ?", (survivor,))
                conn.commit()
                return {"id": survivor, "merged": True}
            conn.execute(
                "UPDATE nodes SET name = ?, human_edited = 1 WHERE id = ?", (norm, node_id)
            )
            self._curation_insert(conn, "alias", kind=kind, name=old_name, canonical_id=node_id)
            conn.commit()
            return {"id": node_id, "merged": False}

    def update_node(
        self,
        node_id: str,
        kind: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> None:
        """Human edit of kind/summary. Marks the node human_edited (protected)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            if row is None:
                raise KeyError(node_id)
            new_kind = (kind or row["kind"]).strip().lower()
            if new_kind != row["kind"]:
                dup = conn.execute(
                    "SELECT 1 FROM nodes WHERE kind = ? AND name = ? AND id != ?",
                    (new_kind, row["name"], node_id),
                ).fetchone()
                if dup:
                    raise ValueError(
                        f"a {new_kind} named '{row['name']}' already exists — rename/merge instead"
                    )
            new_summary = summary if summary is not None else row["summary"]
            conn.execute(
                "UPDATE nodes SET kind = ?, summary = ?, human_edited = 1 WHERE id = ?",
                (new_kind, new_summary, node_id),
            )
            self._curation_insert(
                conn, "edit", kind=new_kind, name=row["name"], canonical_id=node_id,
                payload={"from_kind": row["kind"]},
            )
            conn.commit()

    def delete_node(self, node_id: str) -> None:
        """Delete a node and tombstone its (kind, name) against re-extraction."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            if row is None:
                raise KeyError(node_id)
            self._curation_insert(conn, "tombstone_node", kind=row["kind"], name=row["name"])
            conn.execute("DELETE FROM edges WHERE src = ? OR dst = ?", (node_id, node_id))
            conn.execute("DELETE FROM node_sessions WHERE node_id = ?", (node_id,))
            conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            conn.commit()

    def delete_edge(self, edge_id: str) -> None:
        """Delete an edge and tombstone (src, dst, rel) against re-extraction."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM edges WHERE id = ?", (edge_id,)).fetchone()
            if row is None:
                raise KeyError(edge_id)
            self._curation_insert(
                conn, "tombstone_edge", name=f"{row['src']}|{row['dst']}|{row['rel']}"
            )
            conn.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
            conn.commit()

    def create_node(
        self,
        kind: str,
        name: str,
        summary: Optional[str] = None,
        visibility: str = "company",
    ) -> dict[str, Any]:
        """Manually add a node (source=human → human_edited, protected)."""
        kind = (kind or "").strip().lower()
        norm = normalize_name(name)
        if not kind or not norm:
            raise ValueError("node kind and name are required")
        with self._connect() as conn:
            if self._node_tombstoned(conn, kind, norm):
                raise ValueError(
                    f"({kind}, {norm}) was previously removed — restore is not supported yet"
                )
            existing = conn.execute(
                "SELECT id FROM nodes WHERE kind = ? AND name = ?", (kind, norm)
            ).fetchone()
            if existing:
                nid = existing["id"]
                conn.execute(
                    "UPDATE nodes SET summary = COALESCE(?, summary), human_edited = 1 WHERE id = ?",
                    (summary, nid),
                )
            else:
                nid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO nodes (id, kind, name, summary, visibility, human_edited) "
                    "VALUES (?, ?, ?, ?, ?, 1)",
                    (nid, kind, norm, summary, visibility),
                )
            self._curation_insert(conn, "add", kind=kind, name=norm, canonical_id=nid)
            conn.commit()
        return {"id": nid, "kind": kind, "name": norm, "summary": summary}

    def create_edge(self, src: str, dst: str, rel: str) -> str:
        """Manually add an edge between two existing nodes."""
        with self._connect() as conn:
            for nid in (src, dst):
                if conn.execute("SELECT 1 FROM nodes WHERE id = ?", (nid,)).fetchone() is None:
                    raise KeyError(nid)
        edge_id = self.upsert_edge(src=src, dst=dst, rel=rel)
        with self._connect() as conn:
            self._curation_insert(conn, "add", name=f"{src}|{dst}|{rel}", canonical_id=edge_id)
            conn.commit()
        return edge_id
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_curation.py -v && python -m pytest -q`
Expected: `test_curation.py` all PASS; full suite passes (the enforcement paths only *raise ValueError*, which every machine caller already catches per the existing best-effort contract).

- [ ] **Step 5: Commit**

```bash
git add apps/api/contexthub/graph/store.py apps/api/tests/test_curation.py
git commit -m "feat(graph): curation memory — aliases, tombstones, hard merge, human-edit protection"
```

---

### Task 8: Curation API endpoints

**Files:**
- Modify: `apps/api/contexthub/models.py` (request models)
- Modify: `apps/api/contexthub/api/routes.py` (5 endpoints, after the existing graph endpoints)
- Test: `apps/api/tests/test_curation_api.py` (new)

**Interfaces:**
- `PATCH /v1/graph/nodes/{node_id}` body `{name?, kind?, summary?}` → `{"id", "merged", "node"}`; 404 missing, 409 conflict. Enqueues `entity_resolve` for up to 3 of the node's sessions.
- `DELETE /v1/graph/nodes/{node_id}` → `{"deleted": true}`; 404.
- `POST /v1/graph/nodes` body `{kind, name, summary?}` → 201 `{"id", "kind", "name", "summary"}`; 409 tombstoned.
- `POST /v1/graph/edges` body `{src, dst, rel}` → 201 `{"id"}`; 404 endpoint missing, 409 tombstoned.
- `DELETE /v1/graph/edges/{edge_id}` → `{"deleted": true}`; 404.

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_curation_api.py` (fixture modeled on `tests/test_graph.py`'s `client` fixture — copy its `_clear_caches` helper and env-patch pattern):

```python
"""HTTP surface for graph curation (Task: curation API)."""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer alice-key"}


def _clear_caches() -> None:
    mods = [
        "contexthub.config", "contexthub.embeddings", "contexthub.storage.blob",
        "contexthub.storage.vectors", "contexthub.jobs.store", "contexthub.graph.store",
    ]
    for mod_name in mods:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            for attr in dir(mod):
                fn = getattr(mod, attr, None)
                if callable(fn) and hasattr(fn, "cache_clear"):
                    fn.cache_clear()
    if "contexthub.storage.vectors" in sys.modules:
        sys.modules["contexthub.storage.vectors"].reset_vector_store()
    if "contexthub.graph.store" in sys.modules:
        sys.modules["contexthub.graph.store"].reset_graph_store()


@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": str(tmp_path / "lancedb"),
        "BLOB_DIR": str(tmp_path / "blobs"),
        "JOBS_DB": str(tmp_path / "jobs.db"),
        "GRAPH_DB": str(tmp_path / "graph.db"),
        "API_KEYS": "alice-key:alice:team-red",
        "ANTHROPIC_API_KEY": "",
        "LLM_PROVIDER": "anthropic",
        "S3_BUCKET": "",
        "CORS_ORIGINS": "",
    }
    original = {k: os.environ.get(k) for k in env_patch}
    os.environ.update(env_patch)
    _clear_caches()
    from contexthub.main import create_app

    with TestClient(create_app(), raise_server_exceptions=True) as c:
        yield c
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _clear_caches()


def _mk_node(kind="feature", name="payment-checkout"):
    from contexthub.graph.store import get_graph_store
    return get_graph_store().upsert_node(kind=kind, name=name, session_id="s1")


def test_patch_rename(client):
    nid = _mk_node()
    r = client.patch(f"/v1/graph/nodes/{nid}", json={"name": "checkout"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == nid and body["merged"] is False
    assert body["node"]["name"] == "checkout"


def test_patch_rename_merges_onto_existing(client):
    a = _mk_node(name="payment-checkout")
    b = _mk_node(name="checkout")
    r = client.patch(f"/v1/graph/nodes/{a}", json={"name": "checkout"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {**r.json(), "id": b, "merged": True}


def test_patch_missing_node_404(client):
    r = client.patch("/v1/graph/nodes/nope", json={"name": "x"}, headers=AUTH)
    assert r.status_code == 404


def test_delete_node_then_create_conflicts(client):
    nid = _mk_node(kind="service", name="turn-your-api")
    r = client.delete(f"/v1/graph/nodes/{nid}", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    r = client.post("/v1/graph/nodes",
                    json={"kind": "service", "name": "turn-your-api"}, headers=AUTH)
    assert r.status_code == 409


def test_create_node_and_edge_and_delete_edge(client):
    r = client.post("/v1/graph/nodes",
                    json={"kind": "decision", "name": "sqlite over kuzudb"}, headers=AUTH)
    assert r.status_code == 201
    a = r.json()["id"]
    b = _mk_node(kind="feature", name="graph store")
    r = client.post("/v1/graph/edges", json={"src": a, "dst": b, "rel": "decided"}, headers=AUTH)
    assert r.status_code == 201
    eid = r.json()["id"]
    r = client.delete(f"/v1/graph/edges/{eid}", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    r = client.post("/v1/graph/edges", json={"src": a, "dst": "missing", "rel": "uses"}, headers=AUTH)
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_curation_api.py -v`
Expected: FAIL with 405/404 — endpoints don't exist.

- [ ] **Step 3: Implement**

`models.py` — add after `GraphResponse`:

```python
class GraphNodePatch(BaseModel):
    """PATCH /v1/graph/nodes/{id} — any subset of fields."""

    name: Optional[str] = None
    kind: Optional[str] = None
    summary: Optional[str] = None


class GraphNodeCreate(BaseModel):
    """POST /v1/graph/nodes — manual (human) node."""

    kind: str
    name: str
    summary: Optional[str] = None


class GraphEdgeCreate(BaseModel):
    """POST /v1/graph/edges — manual (human) edge between existing nodes."""

    src: str
    dst: str
    rel: str
```

`routes.py` — add after the `graph_build_progress` endpoint (import the three new models where the other `contexthub.models` imports live):

```python
# ---------------------------------------------------------------------------
# Graph curation (human edits beat machine extraction)
# ---------------------------------------------------------------------------

@router.patch("/v1/graph/nodes/{node_id}", tags=["graph"])
def patch_graph_node(
    node_id: str,
    body: GraphNodePatch,
    request: Request,
    caller: Caller = Depends(require_api_key),
):
    """Edit a node. Renaming onto an existing (kind, name) hard-merges into it;
    the old name becomes an alias so future extraction remaps automatically."""
    from contexthub.graph.store import get_graph_store

    store = get_graph_store()
    result_id, merged = node_id, False
    try:
        if body.name is not None:
            res = store.rename_node(node_id, body.name)
            result_id, merged = res["id"], res["merged"]
        if body.kind is not None or body.summary is not None:
            store.update_node(result_id, kind=body.kind, summary=body.summary)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # The graph changed shape: re-evaluate cross-session same_as links for the
    # sessions this node touches (adaptation — retrieval reads live, resolution
    # re-runs off the request path).
    try:
        job_store = request.app.state.job_store
        for sid in store.sessions_for_node(result_id)[:3]:
            job_store.enqueue(kind="entity_resolve", payload={"session_id": sid})
    except Exception:
        logger.exception("patch_graph_node: failed to enqueue entity_resolve")

    nodes = store.get_nodes([result_id])
    return {"id": result_id, "merged": merged, "node": nodes[0] if nodes else None}


@router.delete("/v1/graph/nodes/{node_id}", tags=["graph"])
def delete_graph_node(node_id: str, caller: Caller = Depends(require_api_key)):
    """Delete a node; its (kind, name) is tombstoned against re-extraction."""
    from contexthub.graph.store import get_graph_store

    try:
        get_graph_store().delete_node(node_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
    return {"deleted": True}


@router.post("/v1/graph/nodes", tags=["graph"], status_code=201)
def create_graph_node(body: GraphNodeCreate, caller: Caller = Depends(require_api_key)):
    """Manually add a node (marked human-edited, protected from extraction)."""
    from contexthub.graph.store import get_graph_store

    try:
        return get_graph_store().create_node(
            kind=body.kind, name=body.name, summary=body.summary
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/v1/graph/edges", tags=["graph"], status_code=201)
def create_graph_edge(body: GraphEdgeCreate, caller: Caller = Depends(require_api_key)):
    """Manually add an edge between two existing nodes."""
    from contexthub.graph.store import get_graph_store

    try:
        edge_id = get_graph_store().create_edge(src=body.src, dst=body.dst, rel=body.rel)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Node {exc} not found.")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": edge_id}


@router.delete("/v1/graph/edges/{edge_id}", tags=["graph"])
def delete_graph_edge(edge_id: str, caller: Caller = Depends(require_api_key)):
    """Delete an edge; (src, dst, rel) is tombstoned against re-extraction."""
    from contexthub.graph.store import get_graph_store

    try:
        get_graph_store().delete_edge(edge_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Edge '{edge_id}' not found.")
    return {"deleted": True}
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_curation_api.py -v && python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/contexthub/models.py apps/api/contexthub/api/routes.py apps/api/tests/test_curation_api.py
git commit -m "feat(api): graph curation endpoints — patch/delete/create nodes and edges"
```

---

### Task 9: Desktop — client methods + editable node panel on GraphPage

**Files:**
- Modify: `apps/desktop/src/lib/graph.ts` (GraphNode type)
- Modify: `apps/desktop/src/lib/api/client.ts` (5 methods)
- Create: `apps/desktop/src/components/GraphNodePanel.tsx`
- Modify: `apps/desktop/src/pages/GraphPage.tsx` (use the panel; generic toggle)
- Test: `apps/desktop/src/lib/api/client.test.ts`

**Interfaces:**
- Client methods (all on the object returned by `makeApiClient`):
  - `updateGraphNode(id: string, patch: {name?: string; kind?: string; summary?: string}): Promise<{id: string; merged: boolean}>`
  - `deleteGraphNode(id: string): Promise<void>`
  - `createGraphNode(node: {kind: string; name: string; summary?: string}): Promise<{id: string}>`
  - `createGraphEdge(edge: {src: string; dst: string; rel: string}): Promise<{id: string}>`
  - `deleteGraphEdge(id: string): Promise<void>`
- `GraphNode` gains `generic?: boolean`.
- `GraphNodePanel` props: `{ node, edges, nodeById, allNodes, localSessionIds, onClose, onSelect, onChanged, client }` where `onChanged: () => void` triggers a graph refetch and `client: ReturnType<typeof makeApiClient>`.

- [ ] **Step 1: Write the failing client tests**

Append to `apps/desktop/src/lib/api/client.test.ts` (reuse the file's `mockFetchOnce` helper):

```ts
describe("graph curation", () => {
  it("PATCHes /v1/graph/nodes/{id} with the edit body", async () => {
    const fn = mockFetchOnce({ id: "n1", merged: false, node: null });
    const client = makeApiClient("http://hub", "k");
    const res = await client.updateGraphNode("n1", { name: "checkout" });
    expect(res).toEqual({ id: "n1", merged: false, node: null });
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe("http://hub/v1/graph/nodes/n1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "checkout" });
  });

  it("DELETEs nodes and edges", async () => {
    let fn = mockFetchOnce({ deleted: true });
    const client = makeApiClient("http://hub", "k");
    await client.deleteGraphNode("n1");
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/nodes/n1");
    expect(fn.mock.calls[0][1].method).toBe("DELETE");
    fn = mockFetchOnce({ deleted: true });
    await client.deleteGraphEdge("e1");
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/edges/e1");
  });

  it("POSTs new nodes and edges", async () => {
    let fn = mockFetchOnce({ id: "n9" });
    const client = makeApiClient("http://hub", "k");
    await client.createGraphNode({ kind: "decision", name: "sqlite over kuzudb" });
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/nodes");
    expect(fn.mock.calls[0][1].method).toBe("POST");
    fn = mockFetchOnce({ id: "e9" });
    await client.createGraphEdge({ src: "a", dst: "b", rel: "uses" });
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/edges");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd apps/desktop && npx vitest run src/lib/api/client.test.ts`
Expected: FAIL — methods don't exist.

- [ ] **Step 3: Implement client methods + type**

`lib/graph.ts` — add to `GraphNode`:

```ts
  generic?: boolean;
```

`lib/api/client.ts` — add next to the other graph methods:

```ts
  /** Edit a graph node. Renaming onto an existing (kind,name) hard-merges. */
  async updateGraphNode(
    id: string,
    patch: { name?: string; kind?: string; summary?: string },
  ): Promise<{ id: string; merged: boolean; node?: unknown }> {
    return this.request("PATCH", `/v1/graph/nodes/${encodeURIComponent(id)}`, patch);
  }

  /** Delete a node; it will not be re-created by future extraction. */
  async deleteGraphNode(id: string): Promise<void> {
    await this.request("DELETE", `/v1/graph/nodes/${encodeURIComponent(id)}`);
  }

  /** Manually add a node. */
  async createGraphNode(node: {
    kind: string;
    name: string;
    summary?: string;
  }): Promise<{ id: string }> {
    return this.request("POST", "/v1/graph/nodes", node);
  }

  /** Manually link two existing nodes. */
  async createGraphEdge(edge: {
    src: string;
    dst: string;
    rel: string;
  }): Promise<{ id: string }> {
    return this.request("POST", "/v1/graph/edges", edge);
  }

  /** Delete an edge; it will not be re-created by future extraction. */
  async deleteGraphEdge(id: string): Promise<void> {
    await this.request("DELETE", `/v1/graph/edges/${encodeURIComponent(id)}`);
  }
```

Run: `npx vitest run src/lib/api/client.test.ts` → PASS. Commit checkpoint:

```bash
git add apps/desktop/src/lib/api/client.ts apps/desktop/src/lib/graph.ts apps/desktop/src/lib/api/client.test.ts
git commit -m "feat(desktop): graph curation client methods + generic node flag"
```

- [ ] **Step 4: Create `GraphNodePanel.tsx`**

Create `apps/desktop/src/components/GraphNodePanel.tsx`:

```tsx
import { useMemo, useState } from "react";
import { ExternalLink, Pencil, Trash2, X, GitMerge, Plus } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { useToast } from "@/components/ui/Toast";
import { kindColor, type GraphEdge, type GraphNode } from "@/lib/graph";
import type { makeApiClient } from "@/lib/api/client";

const KIND_OPTIONS = ["repo", "service", "feature", "person", "decision", "tool", "pr", "problem"]
  .map((k) => ({ value: k, label: k }));
const REL_OPTIONS = ["worked_on", "decided", "fixed", "uses", "depends_on", "related_to"]
  .map((r) => ({ value: r, label: r.replace(/_/g, " ") }));

export interface GraphNodePanelProps {
  node: GraphNode;
  edges: GraphEdge[];
  nodeById: Map<string, GraphNode>;
  allNodes: GraphNode[];
  localSessionIds: Set<string>;
  client: ReturnType<typeof makeApiClient>;
  onClose: () => void;
  onSelect: (id: string) => void;
  onChanged: () => void;
  onOpenSession?: (sessionId: string) => void;
}

/**
 * Node inspector + editor. View mode mirrors the old read-only panel;
 * edit mode exposes rename (rename onto an existing name = hard merge),
 * kind/summary edits, delete, explicit merge-into, edge delete, and add-edge.
 * Every mutation calls onChanged() so the page refetches the live graph.
 */
export function GraphNodePanel({
  node, edges, nodeById, allNodes, localSessionIds, client,
  onClose, onSelect, onChanged, onOpenSession,
}: GraphNodePanelProps) {
  const { info: toastInfo, error: toastError } = useToast();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(node.name);
  const [kind, setKind] = useState(node.kind);
  const [summary, setSummary] = useState(node.summary ?? "");
  const [mergeTarget, setMergeTarget] = useState("");
  const [linkTarget, setLinkTarget] = useState("");
  const [linkRel, setLinkRel] = useState("related_to");
  const [busy, setBusy] = useState(false);

  const mergeCandidates = useMemo(
    () => allNodes.filter((n) => n.kind === node.kind && n.id !== node.id),
    [allNodes, node],
  );
  const linkCandidates = useMemo(
    () => allNodes.filter((n) => n.id !== node.id),
    [allNodes, node],
  );

  const run = async (fn: () => Promise<unknown>, okMsg: string) => {
    setBusy(true);
    try {
      await fn();
      toastInfo(okMsg);
      onChanged();
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Graph update failed.");
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      const patch: { name?: string; kind?: string; summary?: string } = {};
      if (name.trim() && name.trim() !== node.name) patch.name = name.trim();
      if (kind !== node.kind) patch.kind = kind;
      if (summary !== (node.summary ?? "")) patch.summary = summary;
      if (Object.keys(patch).length) await client.updateGraphNode(node.id, patch);
      setEditing(false);
    }, "Node updated — future extractions follow the new name.");

  const mergeInto = () => {
    const target = nodeById.get(mergeTarget);
    if (!target) return;
    return run(
      () => client.updateGraphNode(node.id, { name: target.name }),
      `Merged into "${target.name}".`,
    );
  };

  const remove = () => {
    if (!window.confirm(`Delete "${node.name}"? It won't be re-extracted.`)) return;
    return run(async () => {
      await client.deleteGraphNode(node.id);
      onClose();
    }, "Node deleted (tombstoned).");
  };

  const addEdge = () => {
    if (!linkTarget) return;
    return run(
      () => client.createGraphEdge({ src: node.id, dst: linkTarget, rel: linkRel }),
      "Link added.",
    );
  };

  const removeEdge = (edgeId: string) =>
    run(() => client.deleteGraphEdge(edgeId), "Link removed (tombstoned).");

  return (
    <aside className="w-[320px] shrink-0 border-l border-border bg-bg-elevated overflow-y-auto">
      <div className="flex items-start justify-between gap-2 px-4 py-4 border-b border-border">
        <div className="min-w-0">
          <Badge className="mb-1.5" color="default">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: kindColor(node.kind).ink }}
            />
            {kindColor(node.kind).label}
            {node.generic ? " · common" : ""}
          </Badge>
          <h2 className="text-h3 font-semibold text-ink break-words">{node.name}</h2>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setEditing((v) => !v)}
            aria-label="Edit node"
            className="p-1 rounded-[6px] text-ink-faint hover:text-ink hover:bg-bg-sunken transition-colors duration-120"
          >
            <Pencil size={14} />
          </button>
          <button
            onClick={onClose}
            aria-label="Close panel"
            className="p-1 rounded-[6px] text-ink-faint hover:text-ink hover:bg-bg-sunken transition-colors duration-120"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      <div className="px-4 py-4 space-y-5">
        {editing ? (
          <div className="space-y-3">
            <Input value={name} onChange={(e) => setName(e.target.value)} aria-label="Node name" />
            <Select
              options={KIND_OPTIONS}
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              aria-label="Node kind"
            />
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={3}
              placeholder="Summary"
              aria-label="Node summary"
              className="w-full rounded-[8px] border border-border bg-bg px-3 py-2 text-small text-ink"
            />
            <div className="flex gap-2">
              <Button variant="primary" size="sm" loading={busy} onClick={() => void save()}>
                Save
              </Button>
              <Button variant="secondary" size="sm" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button variant="secondary" size="sm" onClick={() => void remove()}>
                <Trash2 size={13} /> Delete
              </Button>
            </div>

            {mergeCandidates.length > 0 && (
              <div className="pt-2 border-t border-border space-y-2">
                <span className="text-micro uppercase tracking-wide text-ink-faint flex items-center gap-1">
                  <GitMerge size={11} /> Merge into
                </span>
                <Select
                  options={[{ value: "", label: "Pick a node…" }].concat(
                    mergeCandidates.map((n) => ({ value: n.id, label: n.name })),
                  )}
                  value={mergeTarget}
                  onChange={(e) => setMergeTarget(e.target.value)}
                  aria-label="Merge target"
                />
                <Button
                  variant="secondary" size="sm" loading={busy}
                  onClick={() => void mergeInto()}
                >
                  Merge (moves links + sessions)
                </Button>
              </div>
            )}

            <div className="pt-2 border-t border-border space-y-2">
              <span className="text-micro uppercase tracking-wide text-ink-faint flex items-center gap-1">
                <Plus size={11} /> Link to
              </span>
              <Select
                options={[{ value: "", label: "Pick a node…" }].concat(
                  linkCandidates.map((n) => ({ value: n.id, label: `${n.kind}: ${n.name}` })),
                )}
                value={linkTarget}
                onChange={(e) => setLinkTarget(e.target.value)}
                aria-label="Link target"
              />
              <Select
                options={REL_OPTIONS}
                value={linkRel}
                onChange={(e) => setLinkRel(e.target.value)}
                aria-label="Link relation"
              />
              <Button variant="secondary" size="sm" loading={busy} onClick={() => void addEdge()}>
                Add link
              </Button>
            </div>
          </div>
        ) : (
          <>
            {node.summary && (
              <p className="text-small text-ink-soft leading-relaxed">{node.summary}</p>
            )}

            {edges.length > 0 && (
              <div>
                <h3 className="text-micro font-semibold uppercase tracking-wide text-ink-faint mb-2">
                  Relations
                </h3>
                <ul className="space-y-1.5">
                  {edges.map((e) => {
                    const otherId = e.src === node.id ? e.dst : e.src;
                    const other = nodeById.get(otherId);
                    if (!other) return null;
                    return (
                      <li key={e.id} className="flex items-center gap-2">
                        <button
                          onClick={() => onSelect(other.id)}
                          className="flex-1 min-w-0 flex items-center gap-2 text-left text-small text-ink-soft hover:text-ink transition-colors duration-120"
                        >
                          <span
                            className="w-2 h-2 rounded-full shrink-0 border"
                            style={{
                              backgroundColor: kindColor(other.kind).fill,
                              borderColor: kindColor(other.kind).stroke,
                            }}
                          />
                          <span className="truncate">{other.name}</span>
                          <span className="ml-auto text-micro text-ink-faint shrink-0">
                            {e.rel.replace(/_/g, " ")}
                          </span>
                        </button>
                        <button
                          onClick={() => void removeEdge(e.id)}
                          aria-label={`Remove link to ${other.name}`}
                          className="p-0.5 text-ink-faint hover:text-ink shrink-0"
                        >
                          <X size={11} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}

            <div>
              <h3 className="text-micro font-semibold uppercase tracking-wide text-ink-faint mb-2">
                Linked sessions ({node.sessionIds.length})
              </h3>
              {node.sessionIds.length === 0 ? (
                <p className="text-small text-ink-faint">No session provenance recorded.</p>
              ) : (
                <ul className="space-y-1">
                  {node.sessionIds.map((sid) => (
                    <li key={sid}>
                      {localSessionIds.has(sid) && onOpenSession ? (
                        <button
                          onClick={() => onOpenSession(sid)}
                          className="flex items-center gap-1.5 text-small font-mono text-accent hover:text-accent-ink transition-colors duration-120 max-w-full"
                        >
                          <span className="truncate">{sid}</span>
                          <ExternalLink size={11} className="shrink-0" />
                        </button>
                      ) : (
                        <span className="block text-small font-mono text-ink-faint truncate">
                          {sid}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
```

- [ ] **Step 5: Wire into `GraphPage.tsx`**

1. Import: `import { GraphNodePanel } from "@/components/GraphNodePanel";`
2. Add state near `reviewOpen`: `const [showGeneric, setShowGeneric] = useState(false);`
3. Extend `visibleNodes` to hide generic hubs by default:

```tsx
  const visibleNodes = useMemo(
    () =>
      (data?.nodes ?? []).filter(
        (n) => !rejected.has(entityKey(n.kind, n.name)) && (showGeneric || !n.generic),
      ),
    [data, rejected, showGeneric],
  );
```

4. Add a toggle in the legend bar (inside the legend `<div>`, after the kind chips):

```tsx
              <label className="flex items-center gap-1.5 text-micro text-ink-faint cursor-pointer">
                <input
                  type="checkbox"
                  checked={showGeneric}
                  onChange={(e) => setShowGeneric(e.target.checked)}
                />
                Show common ({(data?.nodes ?? []).filter((n) => n.generic).length})
              </label>
```

5. Replace the entire `{selected && (<aside …>…</aside>)}` block with:

```tsx
        {selected && (
          <GraphNodePanel
            node={selected}
            edges={selectedEdges}
            nodeById={nodeById}
            allNodes={data?.nodes ?? []}
            localSessionIds={localSessionIds}
            client={makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "")}
            onClose={() => setSelectedId(null)}
            onSelect={(id) => setSelectedId(id)}
            onChanged={() => void load()}
            onOpenSession={(sid) => navigate(`/sessions/${sid}`)}
          />
        )}
```

(The now-unused `Badge`, `ExternalLink`, and `X` imports may remain used elsewhere in the file — remove only if the linter flags them.)

- [ ] **Step 6: Verify**

Run: `cd apps/desktop && npx tsc --noEmit && npx vitest run`
Expected: type-check clean, all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src/components/GraphNodePanel.tsx apps/desktop/src/pages/GraphPage.tsx
git commit -m "feat(desktop): editable graph node panel — rename/merge/delete/link + generic toggle"
```

---

### Task 10: Desktop — node editing on the per-session SessionGraph

**Files:**
- Modify: `apps/desktop/src/components/SessionGraph.tsx`

**Interfaces:**
- Consumes: `GraphNodePanel` (Task 9) and existing `client.getSessionGraph`.
- Produces: clicking a node in the session subgraph opens the same edit panel; mutations refetch the subgraph.

- [ ] **Step 1: Add selection + panel to `SessionGraph.tsx`**

1. Imports: add `useCallback` to the react import; add `import { GraphNodePanel } from "@/components/GraphNodePanel";` and `import type { GraphNode } from "@/lib/graph";`
2. Add state + refetch: below the existing `useState` hooks:

```tsx
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!settings.apiBaseUrl) return;
    const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
    try {
      setData(await client.getSessionGraph(sessionId));
    } catch {
      /* keep current view */
    }
  }, [sessionId, settings.apiBaseUrl, settings.apiKey]);
```

3. Make nodes clickable — in the node `<g>` render, add:

```tsx
                  className="cursor-pointer"
                  onClick={() => setSelectedId(n.id)}
```

4. Render the panel after the `</svg>`-containing `<div>` (inside the outer `space-y-2` wrapper):

```tsx
      {selectedId && data && nodeById.get(selectedId) && (
        <div className="flex justify-end">
          <GraphNodePanel
            node={nodeById.get(selectedId) as GraphNode}
            edges={data.edges.filter((e) => e.src === selectedId || e.dst === selectedId)}
            nodeById={nodeById}
            allNodes={data.nodes}
            localSessionIds={new Set([sessionId])}
            client={makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "")}
            onClose={() => setSelectedId(null)}
            onSelect={(id) => setSelectedId(id)}
            onChanged={() => void refetch()}
          />
        </div>
      )}
```

5. Also clear the selection when the graph refetches to a state without that node: in `refetch`, after `setData(...)`, add `setSelectedId((cur) => (cur && !g.nodes.some((n) => n.id === cur) ? null : cur));` — capture `const g = await client.getSessionGraph(sessionId);` first, then `setData(g);`.

- [ ] **Step 2: Verify**

Run: `cd apps/desktop && npx tsc --noEmit && npx vitest run`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/src/components/SessionGraph.tsx
git commit -m "feat(desktop): node editing on the per-session graph"
```

---

### Task 11: Rebuild the real graph + eval verification

The existing `data-real` graph is ~100% noise (NER-only, star edges) and predates curation, so nothing human-made is lost by wiping it.

- [ ] **Step 1: Full test suites green**

Run: `cd apps/api && . .venv/bin/activate && python -m pytest -q` and `cd apps/desktop && npx vitest run`
Expected: all pass.

- [ ] **Step 2: Eval harness — confirm retrieval lift is preserved/improved**

Run: `cd apps/api && . .venv/bin/activate && python -m contexthub.eval.run --embedder local --by-type`
Expected: Hit@3 ≥ .90, Bridge Recall@10 = 1.0, Alias MRR = 1.0 (the 2026-06-27 baseline with the graph arm). Record the numbers in this plan file under this step.

- [ ] **Step 3: Wipe and rebuild the real graph** (destructive — confirm with the user before running; the data dir in use is whatever `GRAPH_DB` the running API points at, `apps/api/data-real/graph.db` on the reference machine)

```bash
# with the API stopped:
rm apps/api/data-real/graph.db
# start the API (make api), then kick off the offline build:
curl -X POST http://localhost:8787/v1/graph/build-all -H "Authorization: Bearer dev-key"
# watch progress:
curl -s http://localhost:8787/v1/graph/build-progress -H "Authorization: Bearer dev-key"
```

Expected: NER phase completes in minutes; LLM phase (worthiest third, capped at 300) trickles via `claude-cli`.

- [ ] **Step 4: Spot-check the rebuilt graph**

```bash
sqlite3 apps/api/data-real/graph.db "SELECT kind, COUNT(*) FROM nodes GROUP BY kind;"
sqlite3 apps/api/data-real/graph.db "SELECT name FROM nodes WHERE kind='service';"
sqlite3 apps/api/data-real/graph.db "SELECT rel, COUNT(*) FROM edges GROUP BY rel;"
```

Expected: `feature`/`decision`/`problem` kinds exist; no `turn-your-api`-style slugs; `co_occurs` count far below the old 901 with real `worked_on`/`uses`/`fixed` rels present; `SELECT COUNT(*) FROM nodes WHERE generic=1` > 0 (github/python/s3 flagged).

- [ ] **Step 5: Manual curation smoke test in the desktop app**

Open the Knowledge Graph page: rename a node onto an existing one (merges), delete a garbage node, re-run "Generate graph" — the deleted node must NOT return. Commit any fixes found.

- [ ] **Step 6: Final commit**

```bash
git add -A && git commit -m "chore(graph): rebuild verification notes for extraction overhaul"
```

---

## Self-Review (completed at planning time)

- **Spec coverage:** NER precision → Task 1; generic/DF flag → Tasks 2–3; PPMI edges → Task 4; concept prompt + controlled rels + `problem` kind → Task 5; tiered LLM + better input → Task 6; curation memory + hard merge/alias/tombstone → Task 7; curation API + entity_resolve re-enqueue (adaptation) → Task 8; editable GraphPage/SessionGraph + generic viz toggle + add node/edge → Tasks 9–10; rebuild + eval → Task 11. Note: the spec's "node embedding refreshed (node_vectors)" adaptation is satisfied by re-enqueueing `entity_resolve` (which embeds live) — there is no persistent node-vector table on main today, so nothing else to invalidate.
- **Placeholder scan:** no TBDs; every code step contains the code.
- **Type consistency:** `rename_node` returns `{"id", "merged"}` — used identically in Task 7 tests, Task 8 endpoint, Task 9 client typing. `recompute_generic_flags(fraction, min_total)` matches call sites in Tasks 3, 4. `refresh_cooccur_edges(store, min_cooccur, max_pairs)` matches Task 4 tests and build.py call.
