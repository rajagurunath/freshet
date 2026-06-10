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
from datetime import datetime, timezone
from typing import Any, Optional

import pyarrow as pa

from contexthub.config import Settings, get_settings

logger = logging.getLogger(__name__)

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
    ])


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class VectorStore:
    """Thin wrapper around LanceDB exposing the operations the API needs."""

    def __init__(self, uri: str, embedding_dim: int) -> None:
        import lancedb  # lazy import

        self._uri = uri
        self._dim = embedding_dim
        self._db = lancedb.connect(uri)
        self._chunks_tbl = None
        self._sessions_tbl = None

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
                self._chunks_tbl = self._db.create_table(
                    "chunks",
                    schema=_chunks_schema(self._dim),
                )
        return self._chunks_tbl

    def _get_sessions_table(self):
        if self._sessions_tbl is None:
            if "sessions" in self._existing_table_names():
                self._sessions_tbl = self._db.open_table("sessions")
            else:
                self._sessions_tbl = self._db.create_table(
                    "sessions",
                    schema=_sessions_schema(),
                )
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

        import pyarrow as pa
        batch = pa.RecordBatch.from_pylist([processed], schema=_sessions_schema())
        try:
            (
                tbl.merge_insert("id")
                .when_matched_update_all()
                .when_not_matched_insert_all()
                .execute(batch)
            )
        except Exception:
            logger.exception("upsert_session failed for session %s", row.get("id", "?"))
            raise

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
    # Catalog queries (paginated + sorted)
    # ------------------------------------------------------------------

    def list_sessions(
        self,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "created_at",
        order: str = "desc",
    ) -> dict[str, Any]:
        """Return a paginated, sorted catalog result dict with keys:
        ``items``, ``total``, ``limit``, ``offset``.

        ``sort`` must be one of SORT_FIELDS; unknown values are silently
        replaced with ``created_at``.  ``order`` is ``asc`` or ``desc``.
        """
        if sort not in SORT_FIELDS:
            logger.warning("Unknown sort field %r, defaulting to created_at", sort)
            sort = "created_at"
        if order not in ("asc", "desc"):
            order = "desc"

        tbl = self._get_sessions_table()

        where_clause: Optional[str] = None
        if filters:
            where_parts = []
            for key, val in filters.items():
                if val is not None:
                    safe = str(val).replace("'", "''")
                    where_parts.append(f"{key} = '{safe}'")
            if where_parts:
                where_clause = " AND ".join(where_parts)

        # Fetch all rows matching the filter (LanceDB doesn't yet support
        # SQL-style ORDER BY + LIMIT + OFFSET in a single call on all backends,
        # so we do sort/slice in Python after fetching the matching set).
        try:
            if where_clause:
                arrow_tbl = tbl.search().where(where_clause).limit(100_000).to_arrow()
            else:
                arrow_tbl = tbl.to_arrow()
        except Exception:
            logger.exception("list_sessions scan failed")
            raise

        all_rows: list[dict[str, Any]] = arrow_tbl.to_pylist()
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

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """Return a single catalog row by id, or None."""
        tbl = self._get_sessions_table()
        safe_id = session_id.replace("'", "''")
        rows = tbl.search().where(f"id = '{safe_id}'").limit(1).to_list()
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
