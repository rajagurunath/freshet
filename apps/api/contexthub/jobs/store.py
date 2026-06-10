"""SQLite-backed job queue store.

Schema
------
jobs table:
  id            TEXT PRIMARY KEY (UUID v4)
  kind          TEXT NOT NULL
  payload_json  TEXT NOT NULL (JSON)
  status        TEXT NOT NULL  -- queued | running | done | error
  result_json   TEXT           -- JSON, set on completion
  error         TEXT           -- error message, set on failure
  created_at    TEXT NOT NULL  (ISO-8601 UTC)
  started_at    TEXT           (ISO-8601 UTC)
  finished_at   TEXT           (ISO-8601 UTC)
  scheduled_for TEXT           (ISO-8601 UTC; NULL means "ready now")

claim_next uses a single UPDATE ... WHERE id = (SELECT ...) RETURNING
pattern so concurrent workers do not double-claim the same job.
WAL mode is enabled for read-write concurrency.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    result_json   TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT,
    scheduled_for TEXT
);
"""

_CREATE_IDX_STATUS = "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);"
_CREATE_IDX_KIND   = "CREATE INDEX IF NOT EXISTS idx_jobs_kind   ON jobs(kind);"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    # Deserialise JSON fields
    raw_payload = d.get("payload_json") or "{}"
    raw_result  = d.get("result_json")
    try:
        d["payload"] = json.loads(raw_payload)
    except Exception:
        d["payload"] = {}
    if raw_result:
        try:
            d["result"] = json.loads(raw_result)
        except Exception:
            d["result"] = None
    else:
        d["result"] = None
    # Remove raw JSON columns from the public dict
    d.pop("payload_json", None)
    d.pop("result_json", None)
    return d


class JobStore:
    """Thin SQLite wrapper around the jobs table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # Ensure the parent directory exists (e.g. ./data/)
        import os
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            conn.execute(_CREATE_IDX_STATUS)
            conn.execute(_CREATE_IDX_KIND)
            conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        scheduled_for: Optional[str] = None,
    ) -> str:
        """Insert a new job in 'queued' status and return its id."""
        job_id = str(uuid.uuid4())
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs
                    (id, kind, payload_json, status, created_at, scheduled_for)
                VALUES
                    (?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, kind, json.dumps(payload), now, scheduled_for),
            )
            conn.commit()
        logger.debug("Enqueued job %s kind=%s", job_id, kind)
        return job_id

    def claim_next(self) -> Optional[dict[str, Any]]:
        """Atomically claim the next queued job that is ready to run.

        A job is ready when its scheduled_for is NULL or <= now (UTC).
        Returns the job dict (status already set to 'running') or None.

        Uses a single UPDATE ... WHERE id = (SELECT ...) RETURNING to
        avoid concurrent double-claims.
        """
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE jobs
                SET status = 'running', started_at = ?
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE status = 'queued'
                      AND (scheduled_for IS NULL OR scheduled_for <= ?)
                    ORDER BY created_at ASC
                    LIMIT 1
                )
                RETURNING *
                """,
                (now, now),
            )
            row = cur.fetchone()
            conn.commit()
        if row is None:
            return None
        return _row_to_dict(row)

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        """Mark a job as 'done' with the given result."""
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'done', result_json = ?, finished_at = ?
                WHERE id = ?
                """,
                (json.dumps(result), now, job_id),
            )
            conn.commit()
        logger.debug("Job %s completed", job_id)

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job as 'error' with the given error message."""
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'error', error = ?, finished_at = ?
                WHERE id = ?
                """,
                (error, now, job_id),
            )
            conn.commit()
        logger.debug("Job %s failed: %s", job_id, error)

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        """Return the job dict by id, or None."""
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def list(
        self,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return a list of job dicts, optionally filtered by status and/or kind."""
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?"
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            rows = cur.fetchall()
        return [_row_to_dict(r) for r in rows]
