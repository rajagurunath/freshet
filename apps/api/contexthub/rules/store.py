"""SQLite-backed rules store (Task 14).

Schema
------
rules table:
  id            TEXT PRIMARY KEY (UUID v4)
  text          TEXT NOT NULL   -- the rule text (short, actionable sentence)
  rationale     TEXT            -- one-line explanation
  evidence_json TEXT NOT NULL   -- JSON array of session_ids that evidence this rule
  scope         TEXT            -- optional category label (e.g. commit-style, naming)
  status        TEXT NOT NULL DEFAULT 'proposed'
                                -- proposed | accepted | rejected
  author        TEXT            -- the user whose sessions were mined (provenance)
  created_at    TEXT NOT NULL   (ISO-8601 UTC)
  updated_at    TEXT            (ISO-8601 UTC; set when status changes)

Consent gate
------------
Nothing is exported unless status='accepted'.  Extraction always creates rules in
'proposed' status.  The API exposes explicit accept/reject endpoints so the user
must opt-in to each rule before it appears in exports.

Dedup
-----
Rules are not deduplicated at the DB level (no UNIQUE constraint on text) because
the same rule text may legitimately be proposed from different sessions at different
times.  Instead, deduplication by normalized token overlap is applied in extract.py
*before* inserting, so near-identical rules are never stored twice.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CREATE_RULES = """
CREATE TABLE IF NOT EXISTS rules (
    id            TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    rationale     TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    scope         TEXT,
    status        TEXT NOT NULL DEFAULT 'proposed',
    author        TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT
);
"""

_CREATE_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status);",
    "CREATE INDEX IF NOT EXISTS idx_rules_author ON rules(author);",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RulesStore:
    """Thin SQLite wrapper around the rules table."""

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
            conn.execute(_CREATE_RULES)
            for stmt in _CREATE_IDX:
                conn.execute(stmt)
            conn.commit()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert_rule(
        self,
        text: str,
        rationale: Optional[str] = None,
        evidence: Optional[list[str]] = None,
        scope: Optional[str] = None,
        author: Optional[str] = None,
    ) -> str:
        """Insert a new rule in 'proposed' status.  Returns the rule id."""
        rule_id = str(uuid.uuid4())
        now = _now_iso()
        evidence_json = json.dumps(evidence or [])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rules (id, text, rationale, evidence_json, scope, status, author, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?)
                """,
                (rule_id, text.strip(), rationale, evidence_json, scope, author, now, now),
            )
            conn.commit()
        logger.debug("Upserted rule %s: %.60s", rule_id, text)
        return rule_id

    def set_status(self, rule_id: str, status: str) -> bool:
        """Set the status of a rule (accepted|rejected|proposed).

        Returns True if the rule was found and updated, False otherwise.
        """
        if status not in ("proposed", "accepted", "rejected"):
            raise ValueError(f"Invalid status: {status!r}")
        now = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE rules SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, rule_id),
            )
            conn.commit()
            updated = cur.rowcount > 0
        return updated

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["evidence"] = json.loads(d.get("evidence_json") or "[]")
        except Exception:
            d["evidence"] = []
        d.pop("evidence_json", None)
        return d

    def get_rule(self, rule_id: str) -> Optional[dict[str, Any]]:
        """Return a rule dict by id, or None."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list_rules(
        self,
        status: Optional[str] = None,
        author: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a list of rule dicts, optionally filtered by status/author."""
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if author:
            clauses.append("author = ?")
            params.append(author)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        sql = f"SELECT * FROM rules {where} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count_rules(
        self,
        status: Optional[str] = None,
        author: Optional[str] = None,
    ) -> int:
        """Return the count of rules matching the given filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if author:
            clauses.append("author = ?")
            params.append(author)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT COUNT(*) as cnt FROM rules {where}"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["cnt"]) if row else 0

    def list_accepted_texts(self) -> list[str]:
        """Return the text of all accepted rules (for dedup checks)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT text FROM rules WHERE status = 'accepted'").fetchall()
        return [r["text"] for r in rows]

    def list_all_texts(self) -> list[str]:
        """Return the text of ALL rules (for dedup: avoid re-proposing existing rules)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT text FROM rules").fetchall()
        return [r["text"] for r in rows]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[RulesStore] = None


def get_rules_store() -> RulesStore:
    """Return the process-wide RulesStore singleton (path from Settings.rules_db)."""
    global _store
    if _store is None:
        from contexthub.config import get_settings
        _store = RulesStore(get_settings().rules_db)
    return _store


def reset_rules_store() -> None:
    """Discard the cached store — used in tests that swap the DB path."""
    global _store
    _store = None
