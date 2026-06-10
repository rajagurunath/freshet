"""SQLite-backed asset store (Task 15).

Schema
------
assets table:
  id            TEXT PRIMARY KEY (UUID v4)
  kind          TEXT NOT NULL   -- skill | script | config | prompt
  name          TEXT NOT NULL
  description   TEXT NOT NULL DEFAULT ''
  category      TEXT NOT NULL DEFAULT 'general'
  author        TEXT NOT NULL
  team          TEXT            -- author's team (for team-scoped visibility)
  visibility    TEXT NOT NULL DEFAULT 'company'
                                -- company | team | private
  files_json    TEXT NOT NULL DEFAULT '[]'
                                -- JSON list of original filenames (informational)
  blob_uri      TEXT NOT NULL   -- file://... or s3://... path to the ZIP payload
  version       TEXT NOT NULL DEFAULT '1.0.0'
  created_at    TEXT NOT NULL   (ISO-8601 UTC)

Visibility enforcement (same semantics as sessions):
  company  → visible to all authenticated callers
  team     → visible to callers on the same team (caller_team == asset.team)
  private  → visible only to the owning user (caller_user_id == asset.author)

Signed download tokens
----------------------
Local mode (no S3): a short-lived HMAC token is generated for each download
request.  The token encodes: HMAC-SHA256(secret, asset_id + ":" + expiry)
where ``expiry`` is a UNIX epoch integer.  The download endpoint validates the
token before streaming the file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CREATE_ASSETS = """
CREATE TABLE IF NOT EXISTS assets (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'general',
    author        TEXT NOT NULL,
    team          TEXT,
    visibility    TEXT NOT NULL DEFAULT 'company',
    files_json    TEXT NOT NULL DEFAULT '[]',
    blob_uri      TEXT NOT NULL,
    version       TEXT NOT NULL DEFAULT '1.0.0',
    created_at    TEXT NOT NULL
);
"""

_CREATE_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);",
    "CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);",
    "CREATE INDEX IF NOT EXISTS idx_assets_author ON assets(author);",
    "CREATE INDEX IF NOT EXISTS idx_assets_visibility ON assets(visibility);",
    # FTS virtual table for name + description search
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
        id UNINDEXED,
        name,
        description,
        content=assets,
        content_rowid=rowid
    );
    """,
    # Triggers to keep FTS in sync
    """
    CREATE TRIGGER IF NOT EXISTS assets_fts_insert AFTER INSERT ON assets BEGIN
        INSERT INTO assets_fts(rowid, id, name, description)
        VALUES (new.rowid, new.id, new.name, new.description);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS assets_fts_delete AFTER DELETE ON assets BEGIN
        INSERT INTO assets_fts(assets_fts, rowid, id, name, description)
        VALUES ('delete', old.rowid, old.id, old.name, old.description);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS assets_fts_update AFTER UPDATE ON assets BEGIN
        INSERT INTO assets_fts(assets_fts, rowid, id, name, description)
        VALUES ('delete', old.rowid, old.id, old.name, old.description);
        INSERT INTO assets_fts(rowid, id, name, description)
        VALUES (new.rowid, new.id, new.name, new.description);
    END;
    """,
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _visibility_where(
    caller_user_id: Optional[str],
    caller_team: Optional[str],
    table_alias: str = "",
) -> tuple[str, list[Any]]:
    """Return a (WHERE fragment, params) for visibility enforcement.

    Rules:
      - company  → always visible
      - team     → visible when caller_team == asset.team
      - private  → visible when caller_user_id == asset.author

    Anonymous callers (user_id=None) see only company-wide assets.
    """
    prefix = f"{table_alias}." if table_alias else ""
    if caller_user_id and caller_team:
        fragment = (
            f"({prefix}visibility = 'company' "
            f"OR ({prefix}visibility = 'team' AND {prefix}team = ?) "
            f"OR ({prefix}visibility = 'private' AND {prefix}author = ?))"
        )
        return fragment, [caller_team, caller_user_id]
    elif caller_user_id:
        fragment = (
            f"({prefix}visibility = 'company' "
            f"OR ({prefix}visibility = 'private' AND {prefix}author = ?))"
        )
        return fragment, [caller_user_id]
    else:
        # Anonymous or no user id — only company-wide
        return f"({prefix}visibility = 'company')", []


# ---------------------------------------------------------------------------
# Signed token helpers
# ---------------------------------------------------------------------------

def generate_download_token(
    asset_id: str,
    secret: str,
    ttl_seconds: int = 3600,
) -> tuple[str, int]:
    """Generate an HMAC-SHA256 signed download token.

    Returns:
        (token_hex, expiry_unix_epoch)

    The caller should embed both in the download URL so the endpoint can
    validate them.
    """
    expiry = int(time.time()) + ttl_seconds
    msg = f"{asset_id}:{expiry}".encode()
    token = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return token, expiry


def verify_download_token(
    asset_id: str,
    token: str,
    expiry: int,
    secret: str,
) -> bool:
    """Verify a signed download token.

    Returns True only when:
      1. The token has not expired (expiry > now).
      2. The HMAC matches (constant-time compare).
    """
    if int(time.time()) > expiry:
        return False
    msg = f"{asset_id}:{expiry}".encode()
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# AssetStore
# ---------------------------------------------------------------------------

class AssetStore:
    """Thin SQLite wrapper around the assets table with FTS support."""

    def __init__(self, db_path: str, blob_dir: str) -> None:
        self._db_path = db_path
        self._blob_dir = Path(blob_dir)
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._blob_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_ASSETS)
            for stmt in _CREATE_IDX:
                conn.execute(stmt)
            conn.commit()

    # ------------------------------------------------------------------
    # Blob storage helpers
    # ------------------------------------------------------------------

    def store_blob(self, asset_id: str, data: bytes, filename: str = "payload.zip") -> str:
        """Write a ZIP payload to the local blob dir; return a file:// URI."""
        target = self._blob_dir / asset_id / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"file://{target.resolve()}"

    def get_blob_path(self, blob_uri: str) -> Optional[Path]:
        """Resolve a blob URI to a local path (file:// only).  Returns None for non-local."""
        if blob_uri.startswith("file://"):
            return Path(blob_uri[len("file://"):])
        return None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_asset(
        self,
        kind: str,
        name: str,
        description: str,
        category: str,
        author: str,
        team: Optional[str],
        visibility: str,
        files_json: str,
        blob_uri: str,
        version: str,
    ) -> str:
        """Insert a new asset record.  Returns the asset id (UUID v4)."""
        asset_id = str(uuid.uuid4())
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO assets
                    (id, kind, name, description, category, author, team,
                     visibility, files_json, blob_uri, version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id, kind, name, description, category, author, team,
                    visibility, files_json, blob_uri, version, now,
                ),
            )
            conn.commit()
        logger.debug("Created asset %s (%s): %s", asset_id, kind, name)
        return asset_id

    def delete_asset(self, asset_id: str) -> bool:
        """Delete an asset by id.  Returns True if deleted, False if not found."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
            conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["files"] = json.loads(d.get("files_json") or "[]")
        except Exception:
            d["files"] = []
        return d

    def get_asset(
        self,
        asset_id: str,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
        enforce_visibility: bool = False,
    ) -> Optional[dict[str, Any]]:
        """Return an asset dict by id, or None if not found / not visible."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
        if row is None:
            return None
        if enforce_visibility:
            frag, params = _visibility_where(caller_user_id, caller_team)
            with self._connect() as conn:
                visible = conn.execute(
                    f"SELECT 1 FROM assets WHERE id = ? AND {frag}",
                    [asset_id] + params,
                ).fetchone()
            if visible is None:
                return None
        return self._row_to_dict(row)

    def list_assets(
        self,
        kind: Optional[str] = None,
        category: Optional[str] = None,
        q: Optional[str] = None,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a list of asset dicts matching the given filters.

        Visibility is always enforced (caller_user_id / caller_team control what's visible).
        ``q`` performs full-text search over name + description via FTS5.
        """
        if q:
            return self._list_assets_fts(
                q=q, kind=kind, category=category,
                caller_user_id=caller_user_id, caller_team=caller_team,
                limit=limit, offset=offset,
            )

        clauses: list[str] = []
        params: list[Any] = []

        vis_frag, vis_params = _visibility_where(caller_user_id, caller_team)
        clauses.append(vis_frag)
        params.extend(vis_params)

        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if category:
            clauses.append("category = ?")
            params.append(category)

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([limit, offset])
        sql = f"SELECT * FROM assets {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _list_assets_fts(
        self,
        q: str,
        kind: Optional[str],
        category: Optional[str],
        caller_user_id: Optional[str],
        caller_team: Optional[str],
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """FTS5-powered search over name + description."""
        # Escape FTS5 special chars by wrapping in double quotes
        safe_q = q.replace('"', '""')
        fts_sql = (
            "SELECT a.* FROM assets a "
            "JOIN assets_fts f ON a.id = f.id "
            "WHERE assets_fts MATCH ?"
        )
        fts_params: list[Any] = [f'"{safe_q}"']

        vis_frag, vis_params = _visibility_where(caller_user_id, caller_team, table_alias="a")
        fts_sql += f" AND {vis_frag}"
        fts_params.extend(vis_params)

        if kind:
            fts_sql += " AND a.kind = ?"
            fts_params.append(kind)
        if category:
            fts_sql += " AND a.category = ?"
            fts_params.append(category)

        fts_sql += " ORDER BY a.created_at DESC LIMIT ? OFFSET ?"
        fts_params.extend([limit, offset])

        with self._connect() as conn:
            rows = conn.execute(fts_sql, fts_params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_assets(
        self,
        kind: Optional[str] = None,
        category: Optional[str] = None,
        caller_user_id: Optional[str] = None,
        caller_team: Optional[str] = None,
    ) -> int:
        """Return the total count for pagination."""
        clauses: list[str] = []
        params: list[Any] = []
        vis_frag, vis_params = _visibility_where(caller_user_id, caller_team)
        clauses.append(vis_frag)
        params.extend(vis_params)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if category:
            clauses.append("category = ?")
            params.append(category)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"SELECT COUNT(*) as cnt FROM assets {where}"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["cnt"]) if row else 0

    def list_categories(self) -> list[str]:
        """Return distinct category values present in the assets table."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM assets ORDER BY category"
            ).fetchall()
        return [r["category"] for r in rows]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[AssetStore] = None


def get_asset_store() -> AssetStore:
    """Return the process-wide AssetStore singleton (path from Settings)."""
    global _store
    if _store is None:
        from contexthub.config import get_settings
        settings = get_settings()
        _store = AssetStore(
            db_path=settings.assets_db,
            blob_dir=settings.asset_blob_dir,
        )
    return _store


def reset_asset_store() -> None:
    """Discard the cached store — used in tests that swap the DB path."""
    global _store
    _store = None
