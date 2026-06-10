"""LanceDB vector store wrapper.

Two tables:
  chunks  — searchable text chunks with embeddings
  sessions — catalog / metadata for each ingested session

Tables are created lazily on first use with explicit pyarrow schemas.
Upserts are implemented as delete-then-add for deterministic ids.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

import pyarrow as pa

from contexthub.config import Settings, get_settings

logger = logging.getLogger(__name__)

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
        pa.field("models", pa.string()),          # JSON-encoded list
        pa.field("preview", pa.string()),
        pa.field("created_at", pa.string()),
        pa.field("blob_uri", pa.string()),
        pa.field("summary", pa.string()),
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
            # lancedb >= 0.8 returns a ListTablesResponse object
            resp = self._db.list_tables()
            if hasattr(resp, "tables"):
                return list(resp.tables)
            return list(resp)
        except Exception:
            # Fallback to deprecated table_names()
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
    # Upsert helpers
    # ------------------------------------------------------------------

    def upsert_chunks(self, rows: list[dict[str, Any]]) -> None:
        """Insert or replace chunks identified by their 'id' field."""
        if not rows:
            return
        tbl = self._get_chunks_table()
        ids = [r["id"] for r in rows]
        # Delete existing rows for these ids (deterministic upsert)
        if ids:
            id_list = ", ".join(f"'{i}'" for i in ids)
            try:
                tbl.delete(f"id IN ({id_list})")
            except Exception:
                pass  # table may be empty on first run
        # Coerce vectors to list[float32] expected by pyarrow
        coerced = []
        for r in rows:
            row = dict(r)
            row["vector"] = [float(v) for v in row["vector"]]
            coerced.append(row)
        tbl.add(coerced)

    def upsert_session(self, row: dict[str, Any]) -> None:
        """Insert or replace a catalog row identified by session 'id'."""
        tbl = self._get_sessions_table()
        sid = row["id"]
        try:
            tbl.delete(f"id = '{sid}'")
        except Exception:
            pass
        tbl.add([row])

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
    # Catalog queries
    # ------------------------------------------------------------------

    def list_sessions(self, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Return catalog rows, optionally filtered.

        Uses to_arrow() for full-table scans (no filter) and search().where()
        for filtered queries, since LanceDB's non-vector search().to_list()
        works correctly in both cases.
        """
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

        if where_clause:
            return tbl.search().where(where_clause).limit(1000).to_list()
        else:
            # Full-table scan: to_arrow() is more reliable than search() with no query
            return tbl.to_arrow().to_pylist()

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
        try:
            sessions_arrow = self._get_sessions_table().to_arrow()
            chunks_arrow = self._get_chunks_table().to_arrow()
        except Exception:
            return {
                "total_sessions": 0,
                "total_chunks": 0,
                "sessions_by_tool": {},
                "sessions_by_category": {},
            }

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
