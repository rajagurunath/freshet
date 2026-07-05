"""SQLite-backed knowledge-graph store (Task 13).

Schema
------
nodes table:
  id          TEXT PRIMARY KEY (UUID v4)
  kind        TEXT NOT NULL   -- repo|service|feature|person|decision|tool|pr
  name        TEXT NOT NULL   -- normalized (lowercased, trimmed)
  summary     TEXT
  visibility  TEXT NOT NULL DEFAULT 'company'  -- company|team|private
  author      TEXT            -- owning user_id (for private visibility)
  team        TEXT            -- owning team (for team visibility)
  UNIQUE(kind, name)          -- dedup: one node per (kind, name)

edges table:
  id          TEXT PRIMARY KEY
  src         TEXT NOT NULL   -- nodes.id
  dst         TEXT NOT NULL   -- nodes.id
  rel         TEXT NOT NULL
  session_id  TEXT            -- provenance
  weight      REAL NOT NULL DEFAULT 1.0
  UNIQUE(src, dst, rel)       -- dedup parallel edges; weight accumulates

node_sessions table (provenance, many-to-many):
  node_id     TEXT NOT NULL
  session_id  TEXT NOT NULL
  UNIQUE(node_id, session_id)

Visibility
----------
Every node carries the visibility of the session that produced it.  Reads accept
``caller_user_id`` / ``caller_team`` and apply the same rules as the session
catalog: company → everyone; team → matching team; private → owning author.
A node that gathers provenance from several sessions keeps the *broadest*
visibility seen (company > team > private) so a feature shared into a public
session is not hidden by a later private mention.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    summary     TEXT,
    visibility  TEXT NOT NULL DEFAULT 'company',
    author      TEXT,
    team        TEXT,
    UNIQUE(kind, name)
);
"""

_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id          TEXT PRIMARY KEY,
    src         TEXT NOT NULL,
    dst         TEXT NOT NULL,
    rel         TEXT NOT NULL,
    session_id  TEXT,
    weight      REAL NOT NULL DEFAULT 1.0,
    UNIQUE(src, dst, rel)
);
"""

_CREATE_NODE_SESSIONS = """
CREATE TABLE IF NOT EXISTS node_sessions (
    node_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    UNIQUE(node_id, session_id)
);
"""

_CREATE_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);",
    "CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src);",
    "CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst);",
    "CREATE INDEX IF NOT EXISTS idx_node_sessions_node ON node_sessions(node_id);",
    "CREATE INDEX IF NOT EXISTS idx_node_sessions_session ON node_sessions(session_id);",
]

# Visibility ranking: broader wins when a node is touched by several sessions.
_VIS_RANK = {"private": 0, "team": 1, "company": 2}


def normalize_name(name: str) -> str:
    """Lowercase + trim a node name for dedup."""
    return (name or "").strip().lower()


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


class GraphStore:
    """Thin SQLite wrapper around the nodes/edges tables."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_NODES)
            conn.execute(_CREATE_EDGES)
            conn.execute(_CREATE_NODE_SESSIONS)
            for stmt in _CREATE_IDX:
                conn.execute(stmt)
            _ensure_column(conn, "nodes", "generic", "INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_extract (
                    session_id TEXT PRIMARY KEY,
                    llm_done   INTEGER NOT NULL DEFAULT 0
                );
                """
            )
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
            conn.commit()

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

    # ------------------------------------------------------------------
    # Upserts
    # ------------------------------------------------------------------

    def upsert_node(
        self,
        kind: str,
        name: str,
        session_id: str,
        visibility: str = "company",
        summary: Optional[str] = None,
        author: Optional[str] = None,
        team: Optional[str] = None,
    ) -> str:
        """Insert or merge a node identified by (kind, name).

        Dedup: a node with the same (kind, normalized-name) is reused; its
        session provenance accumulates and its visibility broadens.  Returns the
        node id.
        """
        kind = (kind or "").strip().lower()
        norm = normalize_name(name)
        if not kind or not norm:
            raise ValueError("node kind and name are required")

        with self._connect() as conn:
            if self._node_tombstoned(conn, kind, norm):
                raise ValueError(f"node ({kind}, {norm}) was removed by the user")
            target = self._alias_target(conn, kind, norm)
            if target:
                # The user renamed/merged this name — remap to the canonical node.
                if session_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO node_sessions (node_id, session_id) VALUES (?, ?)",
                        (target, session_id),
                    )
                conn.commit()
                return target
            cur = conn.execute(
                "SELECT id, visibility, summary, human_edited FROM nodes WHERE kind = ? AND name = ?",
                (kind, norm),
            )
            row = cur.fetchone()
            if row is None:
                node_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO nodes (id, kind, name, summary, visibility, author, team)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (node_id, kind, norm, summary, visibility, author, team),
                )
            else:
                node_id = row["id"]
                if not row["human_edited"]:
                    # Broaden visibility if the incoming one is broader. Human-
                    # edited nodes keep their fields (machine never overwrites).
                    cur_vis = row["visibility"] or "company"
                    if _VIS_RANK.get(visibility, 2) > _VIS_RANK.get(cur_vis, 2):
                        new_vis = visibility
                        new_author = author
                        new_team = team
                    else:
                        new_vis = cur_vis
                        new_author = None  # keep existing
                        new_team = None
                    new_summary = summary or row["summary"]
                    if new_author is not None or new_team is not None or new_vis != cur_vis:
                        conn.execute(
                            "UPDATE nodes SET visibility = ?, author = ?, team = ?, summary = ? WHERE id = ?",
                            (new_vis, author if new_vis == visibility else None,
                             team if new_vis == visibility else None, new_summary, node_id),
                        )
                    elif new_summary != row["summary"]:
                        conn.execute("UPDATE nodes SET summary = ? WHERE id = ?", (new_summary, node_id))

            # Record provenance
            if session_id:
                conn.execute(
                    "INSERT OR IGNORE INTO node_sessions (node_id, session_id) VALUES (?, ?)",
                    (node_id, session_id),
                )
            conn.commit()
        return node_id

    def upsert_edge(
        self,
        src: str,
        dst: str,
        rel: str,
        session_id: Optional[str] = None,
        weight: float = 1.0,
    ) -> str:
        """Insert or merge an edge identified by (src, dst, rel).

        On duplicate, the edge weight is incremented so repeatedly-observed
        relations rank higher.  Returns the edge id.
        """
        rel = (rel or "").strip().lower()
        if not src or not dst or not rel:
            raise ValueError("edge src, dst and rel are required")
        with self._connect() as conn:
            if self._edge_tombstoned(conn, src, dst, rel):
                raise ValueError("edge was removed by the user")
            cur = conn.execute(
                "SELECT id, weight FROM edges WHERE src = ? AND dst = ? AND rel = ?",
                (src, dst, rel),
            )
            row = cur.fetchone()
            if row is None:
                edge_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO edges (id, src, dst, rel, session_id, weight) VALUES (?, ?, ?, ?, ?, ?)",
                    (edge_id, src, dst, rel, session_id, weight),
                )
            else:
                edge_id = row["id"]
                conn.execute(
                    "UPDATE edges SET weight = weight + ? WHERE id = ?",
                    (weight, edge_id),
                )
            conn.commit()
        return edge_id

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    @staticmethod
    def _visibility_clause(
        caller_user_id: Optional[str],
        caller_team: Optional[str],
        prefix: str = "",
    ) -> tuple[str, list[Any]]:
        """Return a (sql, params) pair enforcing visibility rules on nodes.

        ``prefix`` is an optional table alias prefix (e.g. "n.").
        """
        p = prefix
        parts = [f"{p}visibility = 'company'"]
        params: list[Any] = []
        if caller_team is not None:
            parts.append(f"({p}visibility = 'team' AND {p}team = ?)")
            params.append(caller_team)
        if caller_user_id is not None:
            parts.append(f"({p}visibility = 'private' AND {p}author = ?)")
            params.append(caller_user_id)
        return "(" + " OR ".join(parts) + ")", params

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _row_to_node(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "name": row["name"],
            "summary": row["summary"],
            "visibility": row["visibility"],
            "generic": bool(row["generic"]) if "generic" in row.keys() else False,
        }

    def list_nodes(
        self,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Return all visible nodes."""
        clause, params = self._visibility_clause(caller_user_id, caller_team)
        sql = f"SELECT * FROM nodes WHERE {clause} LIMIT ?"
        params = list(params) + [limit]
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    def _visible_node_ids(
        self,
        conn: sqlite3.Connection,
        caller_user_id: Optional[str],
        caller_team: Optional[str],
    ) -> set[str]:
        clause, params = self._visibility_clause(caller_user_id, caller_team)
        rows = conn.execute(f"SELECT id FROM nodes WHERE {clause}", params).fetchall()
        return {r["id"] for r in rows}

    def list_edges(
        self,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        """Return edges whose both endpoints are visible to the caller."""
        with self._connect() as conn:
            visible = self._visible_node_ids(conn, caller_user_id, caller_team)
            rows = conn.execute("SELECT * FROM edges LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            if r["src"] in visible and r["dst"] in visible:
                out.append({
                    "id": r["id"], "src": r["src"], "dst": r["dst"],
                    "rel": r["rel"], "weight": r["weight"], "session_id": r["session_id"],
                })
        return out

    def sessions_for_node(self, node_id: str) -> list[str]:
        """Return the session ids that contributed to a node."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id FROM node_sessions WHERE node_id = ?", (node_id,)
            ).fetchall()
        return [r["session_id"] for r in rows]

    def session_ids_with_nodes(self) -> list[str]:
        """Distinct session ids that contributed at least one node to the graph.

        Used by resolve-backfill to target every session whose graph actually
        exists, independent of the catalog's ``graph_extracted`` bookkeeping.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT session_id FROM node_sessions"
            ).fetchall()
        return [r["session_id"] for r in rows if r["session_id"]]

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

    def node_ids_for_session(self, session_id: str) -> list[str]:
        """Return the ids of every node that carries the given session provenance.

        Unlike ``session_subgraph`` this applies no visibility filter: it is used
        by the internal entity-resolution pass, which must see every node the
        session produced regardless of who can read it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT node_id FROM node_sessions WHERE session_id = ?", (session_id,)
            ).fetchall()
        return [r["node_id"] for r in rows]

    def get_nodes_by_kind(
        self,
        kind: str,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return all visible nodes of a single ``kind``.

        Reuses ``_visibility_clause`` so the same company/team/private rules as
        every other read apply.  Used by entity resolution to gather same-kind
        candidates for blocking.
        """
        kind = (kind or "").strip().lower()
        if not kind:
            return []
        clause, params = self._visibility_clause(caller_user_id, caller_team)
        sql = f"SELECT * FROM nodes WHERE kind = ? AND {clause}"
        with self._connect() as conn:
            rows = conn.execute(sql, [kind] + list(params)).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes(self, ids: Any) -> list[dict[str, Any]]:
        """Fetch full node rows by id with no visibility filter and no limit.

        Used by the internal entity-resolution pass, which must see every node a
        session produced regardless of who can read it (``list_nodes`` would drop
        team/private rows and truncate at its 2000 limit). Ids are de-duplicated
        and chunked to stay under SQLite's bound-parameter ceiling.
        """
        ordered = [i for i in dict.fromkeys(ids) if i]
        if not ordered:
            return []
        out: list[dict[str, Any]] = []
        with self._connect() as conn:
            for start in range(0, len(ordered), 500):
                chunk = ordered[start : start + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT * FROM nodes WHERE id IN ({placeholders})", chunk
                ).fetchall()
                out.extend(self._row_to_node(r) for r in rows)
        return out

    def edges_by_rel(self, rel: str) -> list[tuple[str, str]]:
        """Return every ``(src, dst)`` pair for edges of a relation, unfiltered.

        No visibility clause: entity resolution and ``same_as`` component walks
        must consider links between non-company nodes too.
        """
        rel = (rel or "").strip().lower()
        if not rel:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT src, dst FROM edges WHERE LOWER(rel) = ?", (rel,)
            ).fetchall()
        return [(r["src"], r["dst"]) for r in rows]

    def delete_edges_by_rel(self, rel: str) -> int:
        """Delete every edge with the given relation. Returns rows deleted."""
        rel = (rel or "").strip().lower()
        if not rel:
            return 0
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM edges WHERE LOWER(rel) = ?", (rel,))
            conn.commit()
            return cur.rowcount

    def find_nodes_by_terms(
        self,
        terms: list[str],
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Find visible nodes whose normalized name contains any of the terms.

        Each term is lowercased; an empty term list returns [].
        """
        terms = [normalize_name(t) for t in terms if normalize_name(t)]
        if not terms:
            return []
        vis_clause, vis_params = self._visibility_clause(caller_user_id, caller_team)
        like_clause = " OR ".join(["name LIKE ?"] * len(terms))
        params: list[Any] = list(vis_params) + [f"%{t}%" for t in terms] + [limit]
        sql = f"SELECT * FROM nodes WHERE {vis_clause} AND ({like_clause}) LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_node(r) for r in rows]

    def neighbors(
        self,
        node_id: str,
        depth: int = 1,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return the subgraph reachable from ``node_id`` within ``depth`` hops.

        Visibility is enforced: only visible nodes/edges are traversed/returned.
        Result: {"nodes": [...], "edges": [...]}.
        """
        depth = max(0, int(depth))
        with self._connect() as conn:
            visible = self._visible_node_ids(conn, caller_user_id, caller_team)
            if node_id not in visible:
                return {"nodes": [], "edges": []}

            frontier = {node_id}
            seen_nodes = {node_id}
            collected_edges: dict[str, dict[str, Any]] = {}

            all_edges = conn.execute("SELECT * FROM edges").fetchall()
            adj: dict[str, list[sqlite3.Row]] = {}
            for e in all_edges:
                adj.setdefault(e["src"], []).append(e)
                adj.setdefault(e["dst"], []).append(e)

            for _ in range(depth):
                next_frontier: set[str] = set()
                for nid in frontier:
                    for e in adj.get(nid, []):
                        if e["src"] not in visible or e["dst"] not in visible:
                            continue
                        collected_edges[e["id"]] = {
                            "id": e["id"], "src": e["src"], "dst": e["dst"],
                            "rel": e["rel"], "weight": e["weight"], "session_id": e["session_id"],
                        }
                        other = e["dst"] if e["src"] == nid else e["src"]
                        if other not in seen_nodes:
                            seen_nodes.add(other)
                            next_frontier.add(other)
                frontier = next_frontier
                if not frontier:
                    break

            node_rows = conn.execute(
                f"SELECT * FROM nodes WHERE id IN ({','.join('?' * len(seen_nodes))})",
                tuple(seen_nodes),
            ).fetchall() if seen_nodes else []

        return {
            "nodes": [self._row_to_node(r) for r in node_rows],
            "edges": list(collected_edges.values()),
        }

    def session_subgraph(
        self,
        session_id: str,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return all visible nodes/edges that carry the given session_id provenance."""
        with self._connect() as conn:
            visible = self._visible_node_ids(conn, caller_user_id, caller_team)
            node_ids = [
                r["node_id"]
                for r in conn.execute(
                    "SELECT node_id FROM node_sessions WHERE session_id = ?", (session_id,)
                ).fetchall()
                if r["node_id"] in visible
            ]
            nodes = []
            if node_ids:
                rows = conn.execute(
                    f"SELECT * FROM nodes WHERE id IN ({','.join('?' * len(node_ids))})",
                    tuple(node_ids),
                ).fetchall()
                nodes = [self._row_to_node(r) for r in rows]

            id_set = set(node_ids)
            edges = []
            for e in conn.execute(
                "SELECT * FROM edges WHERE session_id = ?", (session_id,)
            ).fetchall():
                if e["src"] in id_set and e["dst"] in id_set:
                    edges.append({
                        "id": e["id"], "src": e["src"], "dst": e["dst"],
                        "rel": e["rel"], "weight": e["weight"], "session_id": e["session_id"],
                    })
        return {"nodes": nodes, "edges": edges}


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


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[GraphStore] = None


def get_graph_store() -> GraphStore:
    """Return the process-wide GraphStore singleton (path from Settings.graph_db)."""
    global _store
    if _store is None:
        from contexthub.config import get_settings
        _store = GraphStore(get_settings().graph_db)
    return _store


def reset_graph_store() -> None:
    """Discard the cached store — used in tests that swap the DB path."""
    global _store
    _store = None
