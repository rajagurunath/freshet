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
            conn.commit()

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
            cur = conn.execute(
                "SELECT id, visibility, summary FROM nodes WHERE kind = ? AND name = ?",
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
                # Broaden visibility if the incoming one is broader.
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
