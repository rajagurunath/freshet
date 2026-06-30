"""Tool wiring + JSON-shaping tests against a monkeypatched hub (no network).

These prove that each of the seven AICP tools (a) calls the correct hub route with the
correct method/params/body, (b) shapes the response into the conformant AICP camelCase
wire form, and (c) surfaces the stable AICP error set as a readable AICPToolError.
"""

from __future__ import annotations

import pytest

import freshet_mcp.server as server
from freshet_mcp._client import AICPToolError


@pytest.fixture
def calls(monkeypatch):
    """Monkeypatch hub_request; record calls and return canned JSON per route."""
    recorded: list[dict] = []

    canned = {
        ("GET", "/v1/sessions"): {
            "items": [
                {
                    "id": "sess-claude-1",
                    "tool": "claude-code",
                    "title": "Wire up llm-serving-api",
                    "project": "llm-serving-api",
                    "created_at": "2026-06-30T10:00:00Z",
                    "updated_at": "2026-06-30T11:00:00Z",
                    "message_count": 42,
                    "tokens_input": 1000,
                    "tokens_output": 2000,
                    "summary": "did things",
                    "visibility": "company",
                }
            ]
        },
        ("POST", "/v1/query"): {
            "answer": "The retry budget is 3.",
            "citations": [
                {
                    "session_id": "sess-codex-9",
                    "score": 0.87,
                    "snippet": "retry budget set to 3",
                    "title": "Codex retries",
                    "tool": "codex",
                }
            ],
        },
        ("GET", "/v1/session/sess-claude-1/summary"): {
            "summary": "Built the handoff route.",
            "generatedBy": "hub-catalog",
            "generatedAt": "2026-06-30T10:00:00Z",
        },
        ("GET", "/v1/session/sess-claude-1/recent"): {
            "messages": [
                {"id": "m1", "role": "user", "text": "hi"},
                {"id": "m2", "role": "assistant", "text": "hello"},
            ],
            "cursor": "MQ==",
        },
        ("GET", "/v1/session/sess-claude-1/grep"): {
            "matches": [
                {"messageId": "m2", "offset": 0, "role": "assistant", "snippet": "hello"}
            ]
        },
        ("GET", "/v1/session/sess-claude-1/handoff"): {
            "protocol": "aicp/0.1",
            "session": {"id": "sess-claude-1", "tool": "claude-code", "title": "t"},
            "summary": "s",
            "recent": [],
            "more": {"grep": "session.grep", "stream": "session.stream"},
            "issuedAt": "2026-06-30T12:00:00Z",
            "issuedBy": "freshet-hub",
            "redacted": True,
            "decisions": [],
            "touchedFiles": [],
            "workingSet": {"repos": [], "services": [], "libraries": []},
            "relatedSessions": [],
            "openThreads": [],
            "resumeHint": "Continue …",
        },
    }

    def fake_hub_request(method, path, *, params=None, json_body=None, timeout=60.0):
        recorded.append(
            {"method": method, "path": path, "params": params, "json_body": json_body}
        )
        try:
            return canned[(method, path)]
        except KeyError:  # pragma: no cover - guards test typos
            raise AssertionError(f"unexpected hub call: {method} {path}")

    monkeypatch.setattr(server, "hub_request", fake_hub_request)
    return recorded


def test_session_list_shapes_manifests(calls):
    out = server.session_list(tool="claude-code", limit=10)
    assert calls[0] == {
        "method": "GET",
        "path": "/v1/sessions",
        "params": {"tool": "claude-code", "limit": 10},
        "json_body": None,
    }
    assert "sessions" in out and len(out["sessions"]) == 1
    m = out["sessions"][0]
    # camelCase AICP SessionManifest shape
    for key in (
        "id",
        "tool",
        "title",
        "project",
        "startedAt",
        "endedAt",
        "messageCount",
        "tokens",
        "hasSummary",
        "visibility",
        "source",
    ):
        assert key in m, f"missing manifest key {key}"
    assert m["messageCount"] == 42
    assert m["tokens"] == {"input": 1000, "output": 2000}
    assert m["hasSummary"] is True
    assert m["source"] == "hub"


def test_session_search_renders_hits(calls):
    out = server.session_search("retry budget", limit=5)
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "/v1/query"
    assert calls[0]["json_body"] == {"question": "retry budget", "top_k": 5}
    assert out["answer"] == "The retry budget is 3."
    hit = out["hits"][0]
    assert hit["sessionId"] == "sess-codex-9"
    assert hit["score"] == 0.87
    assert hit["manifest"]["id"] == "sess-codex-9"


def test_session_summary_passthrough(calls):
    out = server.session_summary("sess-claude-1")
    assert calls[0]["path"] == "/v1/session/sess-claude-1/summary"
    assert out["generatedBy"] == "hub-catalog"


def test_session_recent_passthrough(calls):
    out = server.session_recent("sess-claude-1", n=2)
    assert calls[0]["params"] == {"n": 2}
    assert len(out["messages"]) == 2
    assert out["cursor"] == "MQ=="


def test_session_grep_passthrough(calls):
    out = server.session_grep("sess-claude-1", "hello", limit=5)
    assert calls[0]["params"] == {"q": "hello", "limit": 5}
    assert out["matches"][0]["messageId"] == "m2"


def test_session_handoff_returns_packet_verbatim(calls):
    out = server.session_handoff("sess-claude-1", levels="summary,recent", n=20)
    assert calls[0]["path"] == "/v1/session/sess-claude-1/handoff"
    assert calls[0]["params"] == {"levels": "summary,recent", "n": 20}
    # Exact AICP envelope keys preserved verbatim.
    for key in (
        "protocol",
        "session",
        "summary",
        "recent",
        "more",
        "issuedAt",
        "issuedBy",
        "redacted",
    ):
        assert key in out, f"missing envelope key {key}"
    assert out["protocol"] == "aicp/0.1"
    assert out["more"] == {"grep": "session.grep", "stream": "session.stream"}
    assert out["redacted"] is True
    # Additive Freshet keys present (superset, not replacement).
    for key in (
        "decisions",
        "touchedFiles",
        "workingSet",
        "relatedSessions",
        "openThreads",
        "resumeHint",
    ):
        assert key in out, f"missing additive key {key}"


def test_session_stream_is_documented_stub(monkeypatch):
    # Stub must not touch the hub at all.
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("session_stream must not call the hub")

    monkeypatch.setattr(server, "hub_request", boom)
    out = server.session_stream("sess-claude-1", from_cursor="abc")
    assert out["route"] == "/v1/session/sess-claude-1/stream"
    assert out["fromCursor"] == "abc"
    assert "fast-follow" in out["note"]


def test_error_maps_to_stable_aicp_code(monkeypatch):
    def fake_hub_request(*a, **k):
        raise AICPToolError("not_found: no such session")

    monkeypatch.setattr(server, "hub_request", fake_hub_request)
    with pytest.raises(AICPToolError) as ei:
        server.session_summary("missing")
    assert str(ei.value).startswith("not_found:")


def test_all_seven_tools_registered():
    # The MCP surface must expose exactly the seven AICP verbs (underscore form).
    import anyio

    names = {t.name for t in anyio.run(server.mcp.list_tools)}
    assert names == {
        "session_list",
        "session_search",
        "session_summary",
        "session_recent",
        "session_grep",
        "session_stream",
        "session_handoff",
    }
