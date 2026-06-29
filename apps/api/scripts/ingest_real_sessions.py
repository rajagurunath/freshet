"""Ingest the user's REAL local Claude Code + Codex sessions into a data dir.

Builds a genuine, searchable index + knowledge graph from actual session history
so the desktop app's Knowledge Graph and Ask-the-Agent pages reflect the user's
own work (services, repos, tools they actually use) rather than synthetic demo
data. Lightweight Python parse of the jsonl transcripts → NormalizedSession →
chunks + MiniLM embeddings + deterministic NER graph (offline, no LLM).

Run pointed at a fresh data dir, e.g.:
    LANCEDB_URI=./data-real/lancedb GRAPH_DB=./data-real/graph.db \
    python -m scripts.ingest_real_sessions --limit 60
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone

from contexthub.embeddings import get_embedder
from contexthub.graph.ner import extract_ner_graph
from contexthub.graph.store import GraphStore
from contexthub.ingest.chunker import build_chunks
from contexthub.models import Message, NormalizedSession
from contexthub.storage.vectors import VectorStore

HOME = os.path.expanduser("~")


def _text_from_content(content) -> str:
    """Flatten a Claude/Codex message 'content' (str or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                t = b.get("text") or b.get("input_text") or b.get("output_text")
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


def parse_claude(path: str) -> dict | None:
    msgs, cwd, ts = [], None, None
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            cwd = o.get("cwd") or cwd
            ts = ts or o.get("timestamp")
            t = o.get("type")
            if t in ("user", "assistant"):
                m = o.get("message") or {}
                txt = _text_from_content(m.get("content", "")).strip()
                if txt:
                    msgs.append((t, txt))
    except Exception:
        return None
    if not msgs:
        return None
    return {"messages": msgs, "cwd": cwd, "ts": ts, "tool": "claude-code", "path": path}


def parse_codex(path: str) -> dict | None:
    msgs, cwd, ts = [], None, None
    try:
        for line in open(path, encoding="utf-8"):
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
                if p.get("type") == "message":
                    role = p.get("role") or "assistant"
                    txt = _text_from_content(p.get("content", "")).strip()
                    if txt and role in ("user", "assistant"):
                        msgs.append((role, txt))
    except Exception:
        return None
    if not msgs:
        return None
    return {"messages": msgs, "cwd": cwd, "ts": ts, "tool": "codex", "path": path}


def to_session(parsed: dict) -> tuple[NormalizedSession, str, str]:
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
    return sess, summary, project


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60, help="most recent N sessions to ingest")
    args = ap.parse_args()

    lancedb_uri = os.environ.get("LANCEDB_URI", "./data-real/lancedb")
    graph_db = os.environ.get("GRAPH_DB", "./data-real/graph.db")
    os.makedirs(os.path.dirname(graph_db) or ".", exist_ok=True)

    claude = glob.glob(f"{HOME}/.claude/projects/**/*.jsonl", recursive=True)
    codex = glob.glob(f"{HOME}/.codex/sessions/**/*.jsonl", recursive=True)
    # most-recent first by mtime
    files = sorted(
        [(p, "claude") for p in claude] + [(p, "codex") for p in codex],
        key=lambda t: os.path.getmtime(t[0]), reverse=True,
    )

    embedder = get_embedder("local")
    vectors = VectorStore(uri=lancedb_uri, embedding_dim=embedder.dim)
    graph = GraphStore(graph_db)

    seen: set[str] = set()
    ingested = 0
    for path, kind in files:
        if ingested >= args.limit:
            break
        parsed = parse_claude(path) if kind == "claude" else parse_codex(path)
        if not parsed:
            continue
        sess, summary, project = to_session(parsed)
        if sess.id in seen:
            continue
        seen.add(sess.id)

        chunks = build_chunks(sess, summary=summary)
        texts = [c.text for c in chunks]
        if not texts:
            continue
        vecs = embedder.embed_texts(texts)
        vectors.upsert_chunks([
            {
                "id": c.id, "session_id": sess.id, "tool": sess.tool,
                "category": "engineering", "author": "me", "team": "",
                "project": project, "visibility": "company",
                "text": c.text, "vector": v,
                "created_at": parsed.get("ts") or "2026-01-01T00:00:00Z",
            }
            for c, v in zip(chunks, vecs)
        ])
        vectors.upsert_session({
            "id": sess.id, "tool": sess.tool, "title": sess.title,
            "category": "engineering", "author": "me", "team": "",
            "project": project, "visibility": "company",
            "message_count": sess.message_count, "models": [sess.tool],
            "preview": sess.preview, "created_at": parsed.get("ts") or "2026-01-01T00:00:00Z",
            "updated_at": parsed.get("ts") or "2026-01-01T00:00:00Z", "blob_uri": "",
            "summary": summary, "content_hash": sess.id,
            "tokens_input": 0, "tokens_output": 0, "tokens_total": 0,
            "graph_extracted": True, "links_json": "[]",
        })
        extract_ner_graph(sess, summary, graph, visibility="company")
        ingested += 1
        if ingested % 10 == 0:
            print(f"  ingested {ingested} sessions…")

    vectors.ensure_fts_index()
    print(f"\nDONE: {ingested} real sessions ingested · {len(graph.list_nodes())} graph nodes "
          f"· {len(graph.list_edges())} edges -> {lancedb_uri}, {graph_db}")


if __name__ == "__main__":
    main()
