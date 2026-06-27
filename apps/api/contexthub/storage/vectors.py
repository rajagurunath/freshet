"""LanceDB vector store wrapper.

Two tables:
  chunks  — searchable text chunks with embeddings
  sessions — catalog / metadata for each ingested session

Tables are created lazily on first use with explicit pyarrow schemas.
Upserts are atomic using merge_insert (matched → update_all, unmatched → insert_all).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

import pyarrow as pa

from contexthub.config import Settings, get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def rrf(rank_lists: list[list[str]], k: int = 60) -> list[str]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion.

    Each item's fused score is the sum of 1/(k + rank + 1) across all lists
    that contain it (1-indexed rank).  Returns a list of ids sorted by fused
    score descending.

    Args:
        rank_lists: Each inner list is an ordered sequence of item ids (best
                    ranked first).
        k:          RRF constant — higher values reduce the advantage of
                    top-ranked items.  Default 60 (standard in literature).

    Returns:
        Deduplicated list of ids ordered by fused score descending.
    """
    if not rank_lists:
        return []

    scores: dict[str, float] = defaultdict(float)
    for ranks in rank_lists:
        for i, row_id in enumerate(ranks):
            scores[row_id] += 1.0 / (k + i + 1)

    return sorted(scores, key=lambda x: scores[x], reverse=True)


# ---------------------------------------------------------------------------
# Sort field allowlist
# ---------------------------------------------------------------------------

SORT_FIELDS = frozenset({
    "created_at",
    "updated_at",
    "message_count",
    "tokens_input",
    "tokens_output",
    "tokens_total",
    "project",
    "tool",
    "title",
})

# ---------------------------------------------------------------------------
# PyArrow schemas
# ---------------------------------------------------------------------------

def _chunks_schema(dim: int) -> pa.Schema:
    return pa.schema([
        pa.field("id", pa.string()),              # "{session_id}:{i}"
        pa.field("session_id", pa.string()),
        pa.field("tool", pa.string()),
        pa.field("category", pa.string()),
        pa.field("author", pa.string()),          # author.id
        pa.field("team", pa.string()),            # author's team (for team-scoped visibility)
        pa.field("project", pa.string()),
        pa.field("visibility", pa.string()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
        pa.field("created_at", pa.string()),
    ])


def _sessions_schema() -> pa.Schema:
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("tool", pa.string()),
        pa.field("title", pa.string()),
        pa.field("category", pa.string()),
        pa.field("author", pa.string()),
        pa.field("team", pa.string()),               # author's team (for team-scoped visibility)
        pa.field("project", pa.string()),
        pa.field("visibility", pa.string()),
        pa.field("message_count", pa.int64()),
        pa.field("models", pa.list_(pa.string())),   # list<string> — no JSON encoding
        pa.field("preview", pa.string()),
        pa.field("created_at", pa.string()),
        pa.field("updated_at", pa.string()),
        pa.field("blob_uri", pa.string()),
        pa.field("summary", pa.string()),
        pa.field("content_hash", pa.string()),       # sha256 for idempotency
        pa.field("tokens_input", pa.int64()),
        pa.field("tokens_output", pa.int64()),
        pa.field("tokens_total", pa.int64()),        # denormalised sum for sorting
        pa.field("graph_extracted", pa.bool_()),     # True once graph_extract has run (Task 13)
        pa.field("links_json", pa.string()),          # JSON array of SessionLink dicts (Task 16)
    ])


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class VectorStore:
    """Thin wrapper around LanceDB exposing the operations the API needs."""

    def __init__(self, uri: str, embedding_dim: int) -> None:
        import lancedb  # lazy import
        import threading

        self._uri = uri
        self._dim = embedding_dim
        self._db = lancedb.connect(uri)
        self._chunks_tbl = None
        self._sessions_tbl = None
        # Serialise writes: the singleton store is shared between the request
        # thread and the background job worker thread, and LanceDB merge_insert
        # is not safe under concurrent writers to the same table.
        self._write_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Table access (lazy creation)
    # ------------------------------------------------------------------

    def _existing_table_names(self) -> list[str]:
        """Return the list of existing table names, compatible with all lancedb versions."""
        try:
            resp = self._db.list_tables()
            if hasattr(resp, "tables"):
                return list(resp.tables)
            return list(resp)
        except Exception:
            return list(self._db.table_names())  # type: ignore[attr-defined]

    def _get_chunks_table(self):
        if self._chunks_tbl is None:
            if "chunks" in self._existing_table_names():
                self._chunks_tbl = self._db.open_table("chunks")
            else:
                try:
                    self._chunks_tbl = self._db.create_table(
                        "chunks",
                        schema=_chunks_schema(self._dim),
                    )
                except (ValueError, OSError):
                    # Concurrent creator won the race — open the existing table.
                    self._chunks_tbl = self._db.open_table("chunks")
        return self._chunks_tbl

    def _get_sessions_table(self):
        if self._sessions_tbl is None:
            if "sessions" in self._existing_table_names():
                self._sessions_tbl = self._db.open_table("sessions")
            else:
                try:
                    self._sessions_tbl = self._db.create_table(
                        "sessions",
                        schema=_sessions_schema(),
                    )
                except (ValueError, OSError):
                    # Concurrent creator won the race — open the existing table.
                    self._sessions_tbl = self._db.open_table("sessions")
        return self._sessions_tbl

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def get_session_hash(self, session_id: str) -> Optional[str]:
        """Return the stored content_hash for a session, or None if not found."""
        row = self.get_session(session_id)
        if row is None:
            return None
        return row.get("content_hash")

    # ------------------------------------------------------------------
    # Upsert helpers (atomic merge_insert)
    # ------------------------------------------------------------------

    def upsert_chunks(self, rows: list[dict[str, Any]]) -> None:
        """Insert or replace chunks identified by their 'id' field (atomic)."""
        if not rows:
            return
        tbl = self._get_chunks_table()

        # Coerce vectors to list[float32]
        coerced = []
        for r in rows:
            row = dict(r)
            row["vector"] = [float(v) for v in row["vector"]]
            coerced.append(row)

        import pyarrow as pa
        batch = pa.RecordBatch.from_pylist(coerced, schema=_chunks_schema(self._dim))
        try:
            with self._write_lock:
                (
                    tbl.merge_insert("id")
                    .when_matched_update_all()
                    .when_not_matched_insert_all()
                    .execute(batch)
                )
        except Exception:
            logger.exception("upsert_chunks failed for session %s", rows[0].get("session_id", "?"))
            raise

    def upsert_session(self, row: dict[str, Any]) -> None:
        """Insert or replace a catalog row identified by session 'id' (atomic)."""
        tbl = self._get_sessions_table()

        # Ensure list fields have the right type
        processed = dict(row)
        if isinstance(processed.get("models"), str):
            try:
                processed["models"] = json.loads(processed["models"])
            except Exception:
                processed["models"] = []
        # Default columns that may be absent on rows built before they existed.
        processed.setdefault("graph_extracted", False)
        processed.setdefault("links_json", "[]")

        import pyarrow as pa
        batch = pa.RecordBatch.from_pylist([processed], schema=_sessions_schema())
        try:
            with self._write_lock:
                (
                    tbl.merge_insert("id")
                    .when_matched_update_all()
                    .when_not_matched_insert_all()
                    .execute(batch)
                )
        except Exception:
            logger.exception("upsert_session failed for session %s", row.get("id", "?"))
            raise

    def mark_graph_extracted(self, session_id: str) -> None:
        """Flag a session's catalog row as having had graph extraction run.

        Read-modify-write is performed under the write lock so a concurrent
        ingest of the same session is not lost.
        """
        with self._write_lock:
            row = self.get_session(session_id)
            if not row:
                return
            updated = dict(row)
            updated["graph_extracted"] = True
            self.upsert_session(updated)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query_vec: list[float],
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """Vector search the chunks table; returns list of row dicts."""
        tbl = self._get_chunks_table()
        q = tbl.search(query_vec).limit(top_k)
        if filters:
            where_parts = []
            for key, val in filters.items():
                if val is not None:
                    safe = str(val).replace("'", "''")
                    where_parts.append(f"{key} = '{safe}'")
            if where_parts:
                q = q.where(" AND ".join(where_parts))
        results = q.to_list()
        return results

    # ------------------------------------------------------------------
    # FTS index management
    # ------------------------------------------------------------------

    def ensure_fts_index(self) -> None:
        """Create (or recreate) the FTS index on chunks.text.

        Uses LanceDB's native FTS (use_tantivy=False).  The index is created
        with stemming and stop-word removal disabled so that exact identifiers
        (e.g. ERR_5021_FOO) are always retrievable verbatim.

        This is called after batch upserts rather than per-request to amortise
        the index-build cost.  Calling it when the index already exists is safe
        because replace=True drops and recreates it.
        """
        tbl = self._get_chunks_table()
        try:
            tbl.create_fts_index(
                "text",
                replace=True,
                use_tantivy=False,
                # Preserve original casing and skip stemming / stop-word
                # removal so that exact tokens like ERR_5021_FOO are indexed.
                lower_case=False,
                stem=False,
                remove_stop_words=False,
            )
            logger.debug("FTS index created/refreshed on chunks.text")
        except Exception:
            logger.exception("ensure_fts_index failed — FTS search will be unavailable")
            raise

    # ------------------------------------------------------------------
    # Hybrid search (vector + FTS + RRF)
    # ------------------------------------------------------------------

    def hybrid_search(
        self,
        query: str,
        query_vec: list[float],
        top_k: int,
        filters: Optional[dict[str, Any]] = None,
        mode: str = "hybrid",
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Search chunks using vector, FTS, or hybrid (RRF-fused) strategy.

        Args:
            query:           Raw query string (used for FTS).
            query_vec:       Query embedding (used for vector search).
            top_k:           Maximum number of results to return.
            filters:         Optional metadata equality filters (key→value).
            mode:            "hybrid" (default), "vector", or "keyword".
            caller_user_id:  Authenticated caller's user_id (for visibility).
            caller_team:     Authenticated caller's team (for visibility).

        Returns:
            List of row dicts, each augmented with a ``_score`` field carrying
            the fused RRF score (higher = more relevant).  At most ``top_k``
            results are returned.
        """
        tbl = self._get_chunks_table()
        candidate_limit = top_k * 3  # oversample before RRF merge

        # Build a LanceDB WHERE clause combining metadata filters and visibility
        meta_parts: list[str] = []
        if filters:
            for key, val in filters.items():
                if val is not None:
                    safe = str(val).replace("'", "''")
                    meta_parts.append(f"{key} = '{safe}'")

        vis_clause = self.build_visibility_clause(caller_user_id, caller_team)
        all_parts = meta_parts + [vis_clause]
        where_clause: str = " AND ".join(all_parts)

        # Helper: run vector ANN search
        def _vector_search() -> list[dict[str, Any]]:
            q = tbl.search(query_vec).limit(candidate_limit)
            if where_clause:
                q = q.where(where_clause)
            try:
                rows = q.to_list()
            except Exception:
                logger.exception("Vector search failed")
                return []
            # Stable order: ascending distance, then id — so equal-distance rows
            # don't reorder across processes (keeps fused rankings reproducible).
            rows.sort(key=lambda r: (float(r.get("_distance", 0.0)), str(r.get("id", ""))))
            return rows

        # Helper: run FTS search
        def _fts_search() -> list[dict[str, Any]]:
            try:
                q = tbl.search(query, query_type="fts").limit(candidate_limit)
                if where_clause:
                    q = q.where(where_clause)
                rows = q.to_list()
                # Stable order: descending BM25 score, then id. LanceDB's native
                # FTS returns equal-score docs in process-dependent order; pinning
                # the tie-break makes hybrid search reproducible run-to-run.
                rows.sort(key=lambda r: (-float(r.get("_score", 0.0)), str(r.get("id", ""))))
                return rows
            except Exception:
                # FTS index may not exist yet or query may be malformed;
                # fall back gracefully to an empty result set.
                logger.warning(
                    "FTS search failed (index may need refresh): %s",
                    query,
                    exc_info=True,
                )
                return []

        if mode == "vector":
            vec_rows = _vector_search()
            results_by_id: dict[str, dict[str, Any]] = {r["id"]: r for r in vec_rows}
            ranked_ids = [r["id"] for r in vec_rows]
            fused = ranked_ids[:top_k]
            score_map = {rid: 1.0 / (60 + i + 1) for i, rid in enumerate(fused)}
        elif mode == "keyword":
            fts_rows = _fts_search()
            results_by_id = {r["id"]: r for r in fts_rows}
            ranked_ids = [r["id"] for r in fts_rows]
            fused = ranked_ids[:top_k]
            score_map = {rid: 1.0 / (60 + i + 1) for i, rid in enumerate(fused)}
        else:
            # Hybrid: run both searches and merge with RRF
            vec_rows = _vector_search()
            fts_rows = _fts_search()

            results_by_id = {r["id"]: r for r in vec_rows}
            for r in fts_rows:
                results_by_id.setdefault(r["id"], r)

            vec_rank = [r["id"] for r in vec_rows]
            fts_rank = [r["id"] for r in fts_rows]
            fused_ids = rrf([vec_rank, fts_rank], k=60)

            # Compute per-id RRF scores for the return value
            from collections import defaultdict as _dd
            raw_scores: dict[str, float] = _dd(float)
            for rank_list in [vec_rank, fts_rank]:
                for i, rid in enumerate(rank_list):
                    raw_scores[rid] += 1.0 / (60 + i + 1)

            fused = fused_ids[:top_k]
            score_map = {rid: raw_scores[rid] for rid in fused}

        # Assemble output rows, attaching the fused _score
        output = []
        for rid in fused:
            row = dict(results_by_id[rid])
            row["_score"] = float(score_map.get(rid, 0.0))
            output.append(row)

        return output

    # ------------------------------------------------------------------
    # Visibility filtering
    # ------------------------------------------------------------------

    @staticmethod
    def build_visibility_clause(
        caller_user_id: Optional[str],
        caller_team: Optional[str],
    ) -> str:
        """Return a LanceDB WHERE clause enforcing the visibility rules.

        Rules:
          - company  → always visible to all authenticated callers
          - team     → visible when the row's team equals the caller's team
          - private  → visible only when the row's author equals the caller's user_id

        For anonymous callers (user_id=None, team=None), only company-wide
        sessions are returned.

        The returned clause is safe to combine with other clauses via AND.
        """
        parts: list[str] = ["visibility = 'company'"]

        if caller_team is not None:
            safe_team = caller_team.replace("'", "''")
            parts.append(f"(visibility = 'team' AND team = '{safe_team}')")

        if caller_user_id is not None:
            safe_uid = caller_user_id.replace("'", "''")
            parts.append(f"(visibility = 'private' AND author = '{safe_uid}')")

        return "(" + " OR ".join(parts) + ")"

    # ------------------------------------------------------------------
    # Catalog queries (paginated + sorted)
    # ------------------------------------------------------------------

    def list_sessions(
        self,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "created_at",
        order: str = "desc",
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
        link_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return a paginated, sorted catalog result dict with keys:
        ``items``, ``total``, ``limit``, ``offset``.

        ``sort`` must be one of SORT_FIELDS; unknown values are silently
        replaced with ``created_at``.  ``order`` is ``asc`` or ``desc``.

        Visibility is enforced via a WHERE clause generated by
        ``build_visibility_clause``.  Callers with no user_id/team (anonymous)
        see only company-wide sessions.
        """
        if sort not in SORT_FIELDS:
            logger.warning("Unknown sort field %r, defaulting to created_at", sort)
            sort = "created_at"
        if order not in ("asc", "desc"):
            order = "desc"

        tbl = self._get_sessions_table()

        # Build metadata filter clause
        meta_parts: list[str] = []
        if filters:
            for key, val in filters.items():
                if val is not None:
                    safe = str(val).replace("'", "''")
                    meta_parts.append(f"{key} = '{safe}'")

        # Always enforce visibility
        vis_clause = self.build_visibility_clause(caller_user_id, caller_team)
        all_parts = meta_parts + [vis_clause]
        where_clause: str = " AND ".join(all_parts)

        # Fetch all rows matching the filter (LanceDB doesn't yet support
        # SQL-style ORDER BY + LIMIT + OFFSET in a single call on all backends,
        # so we do sort/slice in Python after fetching the matching set).
        try:
            # Hold the write lock for the scan so the list does not observe a
            # transient empty/partial state during a concurrent merge_insert.
            with self._write_lock:
                arrow_tbl = tbl.search().where(where_clause).limit(100_000).to_arrow()
        except Exception:
            logger.exception("list_sessions scan failed")
            raise

        all_rows: list[dict[str, Any]] = arrow_tbl.to_pylist()

        # Post-filter by link URL if requested (Task 16): links are stored as a
        # JSON array string in the links_json column; scan in Python.
        if link_url:
            filtered: list[dict[str, Any]] = []
            for r in all_rows:
                raw_links = r.get("links_json") or "[]"
                try:
                    links = json.loads(raw_links)
                except Exception:
                    links = []
                if any(lnk.get("url") == link_url for lnk in links):
                    filtered.append(r)
            all_rows = filtered

        total = len(all_rows)

        # Sort
        reverse = (order == "desc")
        def _sort_key(r: dict[str, Any]):
            v = r.get(sort)
            if v is None:
                # Put None values last regardless of direction
                return ("" if not reverse else "\xff\xff")
            return v

        all_rows.sort(key=_sort_key, reverse=reverse)

        # Slice
        items = all_rows[offset: offset + limit]

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_session(
        self,
        session_id: str,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
        enforce_visibility: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Return a single catalog row by id, or None.

        When ``enforce_visibility=True`` the visibility clause is combined with
        the id filter so that rows the caller is not allowed to see are
        returned as None (same effect as 404 in the route).  Pass
        caller_user_id/caller_team from the authenticated Caller.

        When ``enforce_visibility=False`` (default, used internally for hash
        checks and chunk ownership lookups) visibility is not applied.
        """
        tbl = self._get_sessions_table()
        safe_id = session_id.replace("'", "''")
        id_clause = f"id = '{safe_id}'"

        if enforce_visibility:
            vis_clause = self.build_visibility_clause(caller_user_id, caller_team)
            where = f"{id_clause} AND {vis_clause}"
        else:
            where = id_clause

        # Hold the write lock for the read too: a single-row lookup must not
        # observe a transient empty result while a concurrent merge_insert (from
        # the background job worker) is mid-flight on the same shared table.
        with self._write_lock:
            rows = tbl.search().where(where).limit(1).to_list()
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Aggregate statistics across both tables."""
        sessions_arrow = self._get_sessions_table().to_arrow()
        chunks_arrow = self._get_chunks_table().to_arrow()

        by_tool: dict[str, int] = {}
        by_cat: dict[str, int] = {}

        tools_col = sessions_arrow.column("tool").to_pylist() if "tool" in sessions_arrow.schema.names else []
        cats_col = sessions_arrow.column("category").to_pylist() if "category" in sessions_arrow.schema.names else []
        for t in tools_col:
            by_tool[t] = by_tool.get(t, 0) + 1
        for c in cats_col:
            by_cat[c] = by_cat.get(c, 0) + 1

        return {
            "total_sessions": len(sessions_arrow),
            "total_chunks": len(chunks_arrow),
            "sessions_by_tool": by_tool,
            "sessions_by_category": by_cat,
        }


# ---------------------------------------------------------------------------
# Module-level singleton factory
# ---------------------------------------------------------------------------

_store: Optional[VectorStore] = None


def get_vector_store(embedding_dim: int | None = None) -> VectorStore:
    """Return the module-level VectorStore singleton.

    embedding_dim is required on first call; subsequent calls may omit it.
    """
    global _store
    if _store is None:
        from contexthub.embeddings import get_embedder

        settings = get_settings()
        dim = embedding_dim or get_embedder().dim
        _store = VectorStore(uri=settings.lancedb_uri, embedding_dim=dim)
    return _store


def reset_vector_store() -> None:
    """Discard the cached store — used in tests to swap URIs."""
    global _store
    _store = None
