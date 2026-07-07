"""SQLite-backed review store (PR-merge-style session approval).

Schema
------
review_requests table:
  session_id         TEXT PRIMARY KEY  — one review request per pushed session
  author_id          TEXT NOT NULL     — the pushing user (cannot vote on own session)
  author_name        TEXT
  title              TEXT NOT NULL
  category           TEXT NOT NULL
  visibility         TEXT NOT NULL
  summary            TEXT              — the summary shown to reviewers
  status             TEXT NOT NULL     — pending | approved | rejected
  approvals_required INTEGER NOT NULL  — snapshot of settings at push time
  created_at         TEXT NOT NULL     (ISO-8601 UTC)
  updated_at         TEXT              (ISO-8601 UTC)
  decided_at         TEXT              (ISO-8601 UTC; set on approve/reject)

review_votes table:
  id            TEXT PRIMARY KEY (UUID v4)
  session_id    TEXT NOT NULL
  reviewer_id   TEXT NOT NULL
  reviewer_name TEXT
  verdict       TEXT NOT NULL — approve | reject
  comment       TEXT
  created_at    TEXT NOT NULL (ISO-8601 UTC)
  UNIQUE(session_id, reviewer_id) — re-voting replaces the prior vote

Transitions (applied by the API layer):
  approve votes >= approvals_required → 'approved' (deferred integration runs)
  any single reject vote              → 'rejected' (blob retained, never indexed)
A re-push of a session under (or after) review resets it to 'pending' and
clears prior votes — new content means a fresh review, like new commits on a PR.
WAL mode is enabled for read-write concurrency (same pattern as jobs/store.py).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CREATE_REQUESTS = """
CREATE TABLE IF NOT EXISTS review_requests (
    session_id         TEXT PRIMARY KEY,
    author_id          TEXT NOT NULL,
    author_name        TEXT,
    title              TEXT NOT NULL,
    category           TEXT NOT NULL,
    visibility         TEXT NOT NULL,
    summary            TEXT,
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK(status IN ('pending','approved','rejected')),
    approvals_required INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT,
    decided_at         TEXT
);
"""

_CREATE_VOTES = """
CREATE TABLE IF NOT EXISTS review_votes (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    reviewer_id   TEXT NOT NULL,
    reviewer_name TEXT,
    verdict       TEXT NOT NULL CHECK(verdict IN ('approve','reject')),
    comment       TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE(session_id, reviewer_id)
);
"""

_CREATE_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_review_requests_status ON review_requests(status);",
    "CREATE INDEX IF NOT EXISTS idx_review_votes_session ON review_votes(session_id);",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewStore:
    """Thin SQLite wrapper around the review_requests + review_votes tables."""

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
            conn.execute(_CREATE_REQUESTS)
            conn.execute(_CREATE_VOTES)
            for stmt in _CREATE_IDX:
                conn.execute(stmt)
            conn.commit()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_request(
        self,
        session_id: str,
        author_id: str,
        author_name: Optional[str],
        title: str,
        category: str,
        visibility: str,
        summary: Optional[str],
        approvals_required: int,
    ) -> None:
        """Create (or reset) a review request in 'pending' status.

        A re-push replaces the request and clears prior votes — new content
        means a fresh review.
        """
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("DELETE FROM review_votes WHERE session_id = ?", (session_id,))
            conn.execute(
                """
                INSERT INTO review_requests
                    (session_id, author_id, author_name, title, category, visibility,
                     summary, status, approvals_required, created_at, updated_at, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, NULL)
                ON CONFLICT(session_id) DO UPDATE SET
                    author_id = excluded.author_id,
                    author_name = excluded.author_name,
                    title = excluded.title,
                    category = excluded.category,
                    visibility = excluded.visibility,
                    summary = excluded.summary,
                    status = 'pending',
                    approvals_required = excluded.approvals_required,
                    updated_at = excluded.updated_at,
                    decided_at = NULL
                """,
                (session_id, author_id, author_name, title, category, visibility,
                 summary, approvals_required, now, now),
            )
            conn.commit()
        logger.debug("Review request created for session %s", session_id)

    def add_vote(
        self,
        session_id: str,
        reviewer_id: str,
        reviewer_name: Optional[str],
        verdict: str,
        comment: Optional[str] = None,
    ) -> str:
        """Record a vote; a reviewer's re-vote replaces their prior vote."""
        if verdict not in ("approve", "reject"):
            raise ValueError(f"Invalid verdict: {verdict!r}")
        vote_id = str(uuid.uuid4())
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO review_votes
                    (id, session_id, reviewer_id, reviewer_name, verdict, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, reviewer_id) DO UPDATE SET
                    reviewer_name = excluded.reviewer_name,
                    verdict = excluded.verdict,
                    comment = excluded.comment,
                    created_at = excluded.created_at
                """,
                (vote_id, session_id, reviewer_id, reviewer_name, verdict, comment, now),
            )
            conn.execute(
                "UPDATE review_requests SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            conn.commit()
        return vote_id

    def set_status(self, session_id: str, status: str) -> bool:
        """Set the status of a review request (pending|approved|rejected).

        Returns True if the request was found and updated, False otherwise.
        """
        if status not in ("pending", "approved", "rejected"):
            raise ValueError(f"Invalid status: {status!r}")
        now = _now_iso()
        decided_at = now if status in ("approved", "rejected") else None
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE review_requests SET status = ?, updated_at = ?, decided_at = ? WHERE session_id = ?",
                (status, now, decided_at, session_id),
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_request(self, session_id: str) -> Optional[dict[str, Any]]:
        """Return a review request dict by session id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM review_requests WHERE session_id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_requests(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return review request dicts, optionally filtered by status."""
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        sql = f"SELECT * FROM review_requests {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_requests(self, status: Optional[str] = None) -> int:
        """Return the count of review requests matching the given status."""
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM review_requests {where}", params
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def votes_for(self, session_id: str) -> list[dict[str, Any]]:
        """Return all votes for a session, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_votes WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, int]:
        """Return {pending, approved, rejected} counts."""
        counts = {"pending": 0, "approved": 0, "rejected": 0}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM review_requests GROUP BY status"
            ).fetchall()
        for r in rows:
            counts[r["status"]] = int(r["cnt"])
        return counts


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[ReviewStore] = None


def get_review_store() -> ReviewStore:
    """Return the process-wide ReviewStore singleton (path from Settings.reviews_db)."""
    global _store
    if _store is None:
        from contexthub.config import get_settings
        _store = ReviewStore(get_settings().reviews_db)
    return _store


def reset_review_store() -> None:
    """Discard the cached store — used in tests that swap the DB path."""
    global _store
    _store = None
