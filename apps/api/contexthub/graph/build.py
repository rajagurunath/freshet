"""Offline knowledge-graph builder for local sessions, with progress tracking.

Reads the user's Claude Code + Codex transcripts straight from disk and builds a
per-session NER graph for each, so every session has a graph to visualize — no
hub round-trip, no embeddings. A background worker drives this with live progress
(``done``/``total``) that the desktop polls to render a build progress bar.

Speed choices (the goal: bring build time down):
- regex + gazetteer NER only (``use_spacy=False``) — no model load / inference;
- co-occurrence edges capped per session (bounded writes);
- resumable — sessions already in the graph are skipped.
"""

from __future__ import annotations

import glob
import json
import os
import threading
import time
from typing import Iterator, Optional

from contexthub.models import Message, NormalizedSession

HOME = os.path.expanduser("~")


# ---------------------------------------------------------------------------
# Lightweight transcript parsing (shared with scripts/ingest_real_sessions.py)
# ---------------------------------------------------------------------------

def _content_text(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        for b in v:
            if isinstance(b, dict):
                for k in ("text", "input_text", "output_text"):
                    t = b.get(k)
                    if t:
                        return t
    return ""


def parse_claude(path: str) -> Optional[dict]:
    msgs, cwd, ts = [], None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                cwd = o.get("cwd") or cwd
                ts = ts or o.get("timestamp")
                if o.get("type") in ("user", "assistant"):
                    txt = _content_text((o.get("message") or {}).get("content", "")).strip()
                    if txt:
                        msgs.append((o["type"], txt))
    except Exception:
        return None
    if not msgs:
        return None
    return {"messages": msgs, "cwd": cwd, "ts": ts, "tool": "claude-code", "path": path}


def parse_codex(path: str) -> Optional[dict]:
    msgs, cwd, ts = [], None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "session_meta":
                    p = o.get("payload") or {}
                    cwd = p.get("cwd") or cwd
                    ts = ts or p.get("timestamp")
                elif o.get("type") == "response_item":
                    p = o.get("payload") or {}
                    if p.get("type") == "message" and p.get("role") in ("user", "assistant"):
                        txt = _content_text(p.get("content", "")).strip()
                        if txt:
                            msgs.append((p["role"], txt))
    except Exception:
        return None
    if not msgs:
        return None
    return {"messages": msgs, "cwd": cwd, "ts": ts, "tool": "codex", "path": path}


def to_session(parsed: dict) -> tuple[NormalizedSession, str]:
    sid = os.path.splitext(os.path.basename(parsed["path"]))[0]
    cwd = parsed.get("cwd") or ""
    project = os.path.basename(cwd.rstrip("/")) if cwd else "unknown"
    first_user = next((t for r, t in parsed["messages"] if r == "user"), "")
    title = (first_user.strip().splitlines()[0] if first_user else parsed["tool"])[:80] or parsed["tool"]
    summary = (first_user or title)[:300]
    messages = [
        Message(id=f"{sid}-{i}", role=("user" if r == "user" else "assistant"), text=t)
        for i, (r, t) in enumerate(parsed["messages"])
    ]
    sess = NormalizedSession(
        id=sid, tool=parsed["tool"], title=title, project=project,
        message_count=len(messages), models=[parsed["tool"]],
        preview=summary[:120], messages=messages,
    )
    return sess, summary


def list_session_files() -> list[tuple[str, str]]:
    """Return (path, tool) for every local transcript, newest first, minus agents."""
    claude = glob.glob(f"{HOME}/.claude/projects/**/*.jsonl", recursive=True)
    codex = glob.glob(f"{HOME}/.codex/sessions/**/*.jsonl", recursive=True)
    files = [(p, "claude") for p in claude] + [(p, "codex") for p in codex]
    files = [
        (p, k) for (p, k) in files
        if "/agent-" not in p and not os.path.basename(p).startswith("agent-")
    ]
    files.sort(key=lambda t: os.path.getmtime(t[0]), reverse=True)
    return files


# ---------------------------------------------------------------------------
# Background build with progress
# ---------------------------------------------------------------------------

_progress = {"done": 0, "total": 0, "running": False, "started_at": 0.0, "finished_at": 0.0}
_lock = threading.Lock()
_thread: Optional[threading.Thread] = None


def get_progress() -> dict:
    with _lock:
        return dict(_progress)


def _worker(limit: Optional[int]) -> None:
    from contexthub.graph.ner import extract_ner_graph
    from contexthub.graph.store import get_graph_store

    store = get_graph_store()
    try:
        already = set(store.session_ids_with_nodes())
    except Exception:
        already = set()

    files = list_session_files()
    if limit:
        files = files[:limit]

    with _lock:
        _progress["total"] = len(files)
        _progress["done"] = 0

    for path, kind in files:
        try:
            sid = os.path.splitext(os.path.basename(path))[0]
            if sid not in already:
                parsed = parse_claude(path) if kind == "claude" else parse_codex(path)
                if parsed:
                    sess, summary = to_session(parsed)
                    # regex+gazetteer only (no spaCy) for speed
                    extract_ner_graph(sess, summary, store, visibility="company", use_spacy=False)
        except Exception:
            pass
        finally:
            with _lock:
                _progress["done"] += 1

    with _lock:
        _progress["running"] = False
        _progress["finished_at"] = time.time()


def start_build_all(limit: Optional[int] = None) -> dict:
    """Start (or no-op if already running) the offline graph build for all sessions."""
    global _thread
    with _lock:
        if _progress["running"]:
            return dict(_progress)
        _progress.update(done=0, total=0, running=True, started_at=time.time(), finished_at=0.0)
    _thread = threading.Thread(target=_worker, args=(limit,), daemon=True)
    _thread.start()
    return get_progress()
