"""_client.hub_request error-mapping tests using an httpx MockTransport (no network)."""

from __future__ import annotations

import httpx
import pytest

from freshet_mcp import _client
from freshet_mcp._client import AICPToolError, hub_request


def _mock(monkeypatch, handler):
    """Route all httpx.request calls through a MockTransport running `handler`."""
    transport = httpx.MockTransport(handler)
    real_request = httpx.request

    def fake_request(method, url, **kwargs):
        kwargs.pop("timeout", None)
        with httpx.Client(transport=transport) as c:
            return c.request(method, url, **kwargs)

    monkeypatch.setattr(httpx, "request", fake_request)
    return real_request


def test_ok_returns_json_and_sets_headers(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["agent"] = request.headers.get("x-aicp-agent")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setenv("FRESHET_API_KEY", "secret-key")
    monkeypatch.setenv("FRESHET_AGENT", "codex")
    _mock(monkeypatch, handler)

    out = hub_request("GET", "/v1/session/x/summary")
    assert out == {"ok": True}
    assert seen["auth"] == "Bearer secret-key"
    assert seen["agent"] == "codex"


def test_structured_error_uses_hub_code(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"detail": {"error": "consent_required", "message": "need grant", "hint": "set env"}},
        )

    _mock(monkeypatch, handler)
    with pytest.raises(AICPToolError) as ei:
        hub_request("GET", "/v1/session/x/handoff")
    msg = str(ei.value)
    assert msg.startswith("consent_required:")
    assert "need grant" in msg
    assert "set env" in msg


def test_status_only_error_falls_back_to_status_map(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="nope")

    _mock(monkeypatch, handler)
    with pytest.raises(AICPToolError) as ei:
        hub_request("GET", "/v1/session/missing/summary")
    assert str(ei.value).startswith("not_found:")


def test_transport_failure_is_internal(monkeypatch):
    def boom(method, url, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "request", boom)
    with pytest.raises(AICPToolError) as ei:
        hub_request("GET", "/v1/sessions")
    assert str(ei.value).startswith("internal: hub unreachable")


def test_defaults(monkeypatch):
    monkeypatch.delenv("FRESHET_HUB_URL", raising=False)
    monkeypatch.delenv("FRESHET_API_KEY", raising=False)
    monkeypatch.delenv("FRESHET_AGENT", raising=False)
    assert _client._hub_url() == "http://localhost:8787"
    assert _client._api_key() == "dev-key"
    assert _client._agent() == "mcp"
