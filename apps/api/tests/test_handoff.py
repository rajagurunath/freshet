"""Tests for the AICP live-handoff hub binding (handoff.py + REST routes).

Self-contained and offline: EMBEDDING_PROVIDER=hash, tmp dirs, no LLM/network.
Mirrors tests/test_graph.py fixtures.

Covers:
  * summary / recent / grep / handoff happy paths over a seeded catalog session.
  * Live-unpushed: a disk-only .jsonl resolved by id (source="local").
  * HandoffPacket conformance: exact envelope keys + Freshet extension keys,
    camelCase on the wire, SessionManifest 11 fields.
  * touched_files extraction from a Claude tool_use/Edit block.
  * working_set / decisions / related_sessions from a seeded graph.
  * Redaction of an sk- secret; redacted == True.
  * Consent: 403 consent_required before grant, success after.
  * Errors: unknown id -> 404 not_found; bad cursor -> 400 invalid_cursor.
  * Stream stub: emits >=1 data event + terminating {"done": true}.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient


def _clear_caches() -> None:
    mods = [
        "contexthub.config",
        "contexthub.embeddings",
        "contexthub.storage.blob",
        "contexthub.storage.vectors",
        "contexthub.jobs.store",
        "contexthub.graph.store",
    ]
    for mod_name in mods:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            for attr in dir(mod):
                fn = getattr(mod, attr, None)
                if callable(fn) and hasattr(fn, "cache_clear"):
                    fn.cache_clear()
    if "contexthub.storage.vectors" in sys.modules:
        sys.modules["contexthub.storage.vectors"].reset_vector_store()
    if "contexthub.graph.store" in sys.modules:
        sys.modules["contexthub.graph.store"].reset_graph_store()


@pytest.fixture(scope="module")
def tmp_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield (
            os.path.join(tmpdir, "lancedb"),
            os.path.join(tmpdir, "blobs"),
            os.path.join(tmpdir, "jobs.db"),
            os.path.join(tmpdir, "graph.db"),
        )


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db, graph_db = tmp_dirs
    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "JOBS_DB": jobs_db,
        "GRAPH_DB": graph_db,
        "API_KEYS": "alice-key:alice:team-red,bob-key:bob:team-blue",
        "ANTHROPIC_API_KEY": "",
        "LLM_PROVIDER": "anthropic",
        "S3_BUCKET": "",
        "CORS_ORIGINS": "",
        "FRESHET_CONSENT": "allow",
    }
    original_env = {k: os.environ.get(k) for k in env_patch}
    os.environ.update(env_patch)
    _clear_caches()

    from contexthub.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _clear_caches()


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    """Default: no on-disk transcripts so catalog tests are deterministic/fast.

    Disk-resolution tests override this with their own crafted directory.
    """
    import contexthub.graph.build as build

    monkeypatch.setattr(build, "list_session_files", lambda: [])
    yield


ALICE = {"Authorization": "Bearer alice-key"}
BOB = {"Authorization": "Bearer bob-key"}


def _make_session(session_id: str, *, visibility: str = "company", messages=None) -> dict:
    if messages is None:
        messages = [
            {"id": "m1", "role": "user", "text": "Work on checkout in the API repo. TODO: wire payments."},
            {"id": "m2", "role": "assistant", "text": "Done the checkout flow"},
        ]
    return {
        "session": {
            "id": session_id,
            "tool": "claude-code",
            "title": f"Session {session_id}",
            "project": "proj",
            "message_count": len(messages),
            "models": ["claude-sonnet-4-6"],
            "tokens": {"input": 100, "output": 20},
            "preview": "preview",
            "file_path": f"/x/{session_id}.jsonl",
            "messages": messages,
        },
        "summary": "Built the checkout feature in the api repo.",
        "category": "engineering",
        "visibility": visibility,
        "author": {"id": "alice", "email": "a@x.com", "name": "Alice", "team": "team-red"},
        "redacted": True,
    }


# ---------------------------------------------------------------------------
# Happy paths over a seeded catalog session
# ---------------------------------------------------------------------------

def test_summary_happy_path(client: TestClient):
    client.post("/v1/sessions", json=_make_session("h-sum"), headers=ALICE)
    resp = client.get("/v1/session/h-sum/summary", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"]
    assert data["generatedBy"] == "hub-catalog"
    assert "generatedAt" in data


def test_recent_happy_path_and_cursor(client: TestClient):
    msgs = [{"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant", "text": f"turn {i}"} for i in range(6)]
    client.post("/v1/sessions", json=_make_session("h-recent", messages=msgs), headers=ALICE)
    resp = client.get("/v1/session/h-recent/recent", params={"n": 2}, headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["messages"]) == 2
    assert data["messages"][-1]["text"] == "turn 5"
    assert data["cursor"]  # a window short of the start carries a cursor


def test_handoff_n_zero_returns_empty_recent_not_full_transcript(client: TestClient):
    """D1 regression: n=0 must NOT leak the whole transcript into recent."""
    msgs = [{"id": f"m{i}", "role": "user", "text": f"turn {i}"} for i in range(12)]
    client.post("/v1/sessions", json=_make_session("h-n0", messages=msgs), headers=ALICE)
    pkt = client.get(
        "/v1/session/h-n0/handoff", params={"levels": "summary,recent", "n": 0}, headers=ALICE
    ).json()
    assert pkt["recent"] == [], "n=0 must return no recent messages, never the full session"


def test_grep_happy_path(client: TestClient):
    client.post("/v1/sessions", json=_make_session("h-grep"), headers=ALICE)
    resp = client.get("/v1/session/h-grep/grep", params={"q": "checkout"}, headers=ALICE)
    assert resp.status_code == 200, resp.text
    matches = resp.json()["matches"]
    assert matches
    m = matches[0]
    assert {"messageId", "offset", "role", "snippet"} <= set(m.keys())
    assert "checkout" in m["snippet"].lower()


# ---------------------------------------------------------------------------
# HandoffPacket conformance (envelope + extensions + camelCase)
# ---------------------------------------------------------------------------

def test_handoff_packet_conformance(client: TestClient):
    client.post("/v1/sessions", json=_make_session("h-pkt"), headers=ALICE)
    resp = client.get("/v1/session/h-pkt/handoff", headers=ALICE)
    assert resp.status_code == 200, resp.text
    pkt = resp.json()

    # --- AICP envelope (spec §6, exact) ---
    assert pkt["protocol"] == "aicp/0.1"
    for key in ("session", "summary", "recent", "more", "issuedAt", "issuedBy", "redacted"):
        assert key in pkt, f"missing envelope key {key}"
    assert pkt["redacted"] is True
    assert pkt["issuedBy"] == "freshet-local"
    assert pkt["more"] == {"grep": "session.grep", "stream": "session.stream"}

    # --- Freshet extension keys (additive superset) ---
    for key in (
        "decisions",
        "touchedFiles",
        "workingSet",
        "relatedSessions",
        "openThreads",
        "resumeHint",
    ):
        assert key in pkt, f"missing extension key {key}"

    # --- camelCase on the wire ---
    assert "issued_at" not in pkt and "touched_files" not in pkt

    # --- SessionManifest: all 11 fields, camelCase ---
    m = pkt["session"]
    expected = {
        "id", "tool", "title", "project", "startedAt", "endedAt",
        "messageCount", "tokens", "hasSummary", "visibility", "source",
    }
    assert expected <= set(m.keys())
    assert m["source"] == "hub"
    assert m["messageCount"] == 2


def test_handoff_levels_filter(client: TestClient):
    client.post("/v1/sessions", json=_make_session("h-levels"), headers=ALICE)
    resp = client.get("/v1/session/h-levels/handoff", params={"levels": "summary"}, headers=ALICE)
    assert resp.status_code == 200, resp.text
    pkt = resp.json()
    assert pkt["summary"]
    assert pkt["recent"] == []  # "recent" not in levels


# ---------------------------------------------------------------------------
# Live-unpushed (disk-first) resolution
# ---------------------------------------------------------------------------

def _write_claude_jsonl(path: str, *, with_edit: bool = False) -> None:
    lines = [
        {
            "type": "user",
            "cwd": "/Users/dev/liveproj",
            "timestamp": "2026-06-30T00:00:00Z",
            "message": {"content": [{"type": "text", "text": "please edit the auth module"}]},
        },
    ]
    assistant_content = [{"type": "text", "text": "editing now"}]
    if with_edit:
        assistant_content.append(
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/Users/dev/liveproj/auth.py"}}
        )
    lines.append({"type": "assistant", "message": {"content": assistant_content}})
    with open(path, "w", encoding="utf-8") as fh:
        for o in lines:
            fh.write(json.dumps(o) + "\n")


def test_handoff_live_unpushed_disk_first(client: TestClient, monkeypatch, tmp_path):
    sid = "live-unpushed-001"
    jsonl = tmp_path / f"{sid}.jsonl"
    _write_claude_jsonl(str(jsonl))
    import contexthub.graph.build as build

    monkeypatch.setattr(build, "list_session_files", lambda: [(str(jsonl), "claude")])

    resp = client.get(f"/v1/session/{sid}/handoff", headers=ALICE)
    assert resp.status_code == 200, resp.text
    pkt = resp.json()
    assert pkt["session"]["source"] == "local"
    assert pkt["session"]["visibility"] == "private"
    assert pkt["recent"]  # disk messages present


def test_touched_files_extraction(client: TestClient, monkeypatch, tmp_path):
    sid = "live-touched-001"
    jsonl = tmp_path / f"{sid}.jsonl"
    _write_claude_jsonl(str(jsonl), with_edit=True)
    import contexthub.graph.build as build

    monkeypatch.setattr(build, "list_session_files", lambda: [(str(jsonl), "claude")])

    resp = client.get(f"/v1/session/{sid}/handoff", headers=ALICE)
    assert resp.status_code == 200, resp.text
    assert "/Users/dev/liveproj/auth.py" in resp.json()["touchedFiles"]


# ---------------------------------------------------------------------------
# working_set / decisions / related_sessions from a seeded graph
# ---------------------------------------------------------------------------

def test_working_set_decisions_related(client: TestClient):
    client.post("/v1/sessions", json=_make_session("h-ws"), headers=ALICE)
    client.post("/v1/sessions", json=_make_session("h-ws2"), headers=ALICE)

    from contexthub.graph.store import get_graph_store

    store = get_graph_store()
    repo = store.upsert_node(kind="repo", name="myrepo", session_id="h-ws", visibility="company")
    store.upsert_node(kind="service", name="billing", session_id="h-ws", visibility="company")
    store.upsert_node(kind="tool", name="pytest", session_id="h-ws", visibility="company")
    store.upsert_node(
        kind="decision", name="use postgres", session_id="h-ws",
        visibility="company", summary="because acid",
    )
    # Second session shares the repo node -> a related session.
    store.upsert_node(kind="repo", name="myrepo", session_id="h-ws2", visibility="company")

    resp = client.get("/v1/session/h-ws/handoff", headers=ALICE)
    assert resp.status_code == 200, resp.text
    pkt = resp.json()
    ws = pkt["workingSet"]
    assert "myrepo" in ws["repos"]
    assert "billing" in ws["services"]
    assert "pytest" in ws["libraries"]
    decisions = [d["decision"] for d in pkt["decisions"]]
    assert "use postgres" in decisions
    related_ids = {r["id"] for r in pkt["relatedSessions"]}
    assert "h-ws2" in related_ids


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def test_redaction_of_secret(client: TestClient):
    secret_msgs = [
        {"id": "m1", "role": "user", "text": "deploy with key sk-abcdefghij1234567890abcdef please"},
        {"id": "m2", "role": "assistant", "text": "ok"},
    ]
    client.post("/v1/sessions", json=_make_session("h-redact", messages=secret_msgs), headers=ALICE)
    resp = client.get("/v1/session/h-redact/handoff", headers=ALICE)
    assert resp.status_code == 200, resp.text
    pkt = resp.json()
    assert pkt["redacted"] is True
    joined = json.dumps(pkt)
    assert "sk-abcdefghij1234567890abcdef" not in joined
    assert "[REDACTED:API_TOKEN]" in joined


# ---------------------------------------------------------------------------
# Consent gate
# ---------------------------------------------------------------------------

def test_consent_required_then_granted(client: TestClient, monkeypatch):
    client.post("/v1/sessions", json=_make_session("h-consent"), headers=ALICE)

    monkeypatch.setenv("FRESHET_CONSENT", "prompt")
    monkeypatch.delenv("FRESHET_CONSENT_GRANTS", raising=False)
    headers = {**ALICE, "X-AICP-Agent": "codex"}
    resp = client.get("/v1/session/h-consent/summary", headers=headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "consent_required"

    monkeypatch.setenv("FRESHET_CONSENT_GRANTS", "codex")
    resp2 = client.get("/v1/session/h-consent/summary", headers=headers)
    assert resp2.status_code == 200, resp2.text


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

def test_unknown_session_not_found(client: TestClient):
    resp = client.get("/v1/session/does-not-exist-xyz/summary", headers=ALICE)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "not_found"


def test_invalid_cursor(client: TestClient):
    client.post("/v1/sessions", json=_make_session("h-cursor"), headers=ALICE)
    resp = client.get(
        "/v1/session/h-cursor/stream", params={"from_cursor": "!!notbase64!!"}, headers=ALICE
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error"] == "invalid_cursor"


# ---------------------------------------------------------------------------
# Stream stub
# ---------------------------------------------------------------------------

def test_stream_stub_emits_done(client: TestClient):
    client.post("/v1/sessions", json=_make_session("h-stream"), headers=ALICE)
    resp = client.get("/v1/session/h-stream/stream", headers=ALICE)
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "data:" in body
    assert '{"done": true}' in body
