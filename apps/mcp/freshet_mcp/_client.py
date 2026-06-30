"""Thin httpx wrapper over the Freshet hub REST binding with AICP error mapping.

Env (read at call time so tests/monkeypatch can override):
- FRESHET_HUB_URL  (default http://localhost:8787)
- FRESHET_API_KEY  (default "dev-key")  -> Bearer auth
- FRESHET_AGENT    (default "mcp")      -> X-AICP-Agent header (consent + redaction)

HTTP errors are mapped to the stable AICP error set and raised as AICPToolError,
which FastMCP turns into a tool result with isError=True carrying "<code>: <message>".
"""

from __future__ import annotations

import os

import httpx

DEFAULT_HUB_URL = "http://localhost:8787"
DEFAULT_API_KEY = "dev-key"
DEFAULT_AGENT = "mcp"

# HTTP status -> stable AICP error code (used only when the hub does not provide a
# structured detail.error; the hub's own error code always wins when present).
_STATUS_TO_CODE = {
    400: "invalid_cursor",
    403: "forbidden",
    404: "not_found",
    429: "rate_limited",
    500: "internal",
}


class AICPToolError(RuntimeError):
    """Raised on any hub error. FastMCP renders it as isError=True.

    The message is formatted as "<code>: <message>" using the stable AICP error
    set (not_found, forbidden, consent_required, invalid_cursor, rate_limited,
    internal) so consumers see a readable, conformant error string.
    """


def _hub_url() -> str:
    return os.environ.get("FRESHET_HUB_URL", DEFAULT_HUB_URL).rstrip("/")


def _api_key() -> str:
    return os.environ.get("FRESHET_API_KEY", DEFAULT_API_KEY)


def _agent() -> str:
    return os.environ.get("FRESHET_AGENT", DEFAULT_AGENT)


def hub_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: float = 60.0,
) -> dict:
    """Make one authed request to the hub and return its parsed JSON dict.

    Raises AICPToolError("<code>: <message>") on transport failure or any 4xx/5xx,
    preferring the hub's structured detail {"error","message"} when present.
    """
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "X-AICP-Agent": _agent(),
    }
    url = f"{_hub_url()}{path}"
    try:
        resp = httpx.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise AICPToolError(f"internal: hub unreachable ({exc})") from exc

    if resp.status_code >= 400:
        code = _STATUS_TO_CODE.get(resp.status_code, "internal")
        message = resp.text
        try:
            detail = resp.json().get("detail")
            if isinstance(detail, dict):
                code = detail.get("error", code)
                message = detail.get("message", message)
                hint = detail.get("hint")
                if hint:
                    message = f"{message} ({hint})"
            elif isinstance(detail, str):
                message = detail
        except Exception:
            pass
        raise AICPToolError(f"{code}: {message}")

    try:
        return resp.json()
    except Exception as exc:  # pragma: no cover - defensive
        raise AICPToolError(f"internal: hub returned non-JSON response ({exc})") from exc
