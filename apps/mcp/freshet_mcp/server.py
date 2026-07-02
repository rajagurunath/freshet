"""Freshet AICP MCP server — FastMCP (stdio) exposing the seven AICP §6 verbs.

Tool names are the AICP wire verbs with the dot rendered as an underscore:
session.list -> session_list, session.search -> session_search,
session.summary -> session_summary, session.recent -> session_recent,
session.grep -> session_grep, session.stream -> session_stream,
session.handoff -> session_handoff. No other tool names exist.

Each tool is a thin proxy to the matching hub REST route via _client.hub_request.
session_handoff returns the hub's HandoffPacket verbatim (already the conformant
camelCase AICP envelope). session_list / session_search do the trivial AICP-shape
render of the reused /v1/sessions and /v1/query routes. session_stream is a
documented fast-follow stub returning a pointer to the REST SSE route.

NEVER print to stdout anywhere — stdout is the JSON-RPC transport channel.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from freshet_mcp._client import hub_request

mcp = FastMCP(
    "freshet",
    instructions=(
        "Freshet AICP broker: pull live cross-agent handoff context for Claude Code / "
        "Codex sessions. Progressive disclosure — use session_list / session_search to "
        "find a session, session_summary / session_recent for context, and "
        "session_handoff for the full bundle (summary, decisions, touched files, working "
        "set, recent turns, related sessions, resume hint). session_grep is find-in-"
        "session; session_stream replays the transcript. All payloads are AICP-conformant "
        "camelCase; session_handoff returns the spec's HandoffPacket envelope verbatim."
    ),
)


@mcp.tool()
def session_list(
    tool: Optional[str] = None,
    project: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """List sessions as AICP SessionManifest[] (verb session.list, L0).

    Optionally filter by `tool` (claude-code | codex | kilo-code) and `project`.
    Returns {"sessions": [SessionManifest, ...]}.
    """
    params = {
        k: v
        for k, v in {"tool": tool, "project": project, "limit": limit}.items()
        if v is not None
    }
    page = hub_request("GET", "/v1/sessions", params=params)
    rows = page.get("items", page if isinstance(page, list) else [])

    def manifest(r: dict) -> dict:
        return {
            "id": r["id"],
            "tool": r.get("tool", ""),
            "title": r.get("title", ""),
            "project": r.get("project"),
            "startedAt": r.get("createdAt", r.get("created_at")),
            "endedAt": r.get("updatedAt", r.get("updated_at")),
            "messageCount": r.get("messageCount", r.get("message_count", 0)),
            "tokens": {
                "input": r.get("tokensInput", r.get("tokens_input", 0)),
                "output": r.get("tokensOutput", r.get("tokens_output", 0)),
            },
            "hasSummary": bool(r.get("summary")),
            "visibility": r.get("visibility", "private"),
            "source": "hub",
        }

    return {"sessions": [manifest(r) for r in rows]}


@mcp.tool()
def session_search(query: str, limit: int = 8) -> dict:
    """Search sessions (verb session.search, L0/L3).

    Returns {"hits": [SearchHit, ...], "answer": <RAG answer or null>} by reusing
    the hub's /v1/query RAG endpoint and rendering its citations into AICP hits.
    """
    res = hub_request("POST", "/v1/query", json_body={"question": query, "top_k": limit})
    hits = []
    for c in res.get("citations", []):
        sid = c.get("session_id", c.get("sessionId", ""))
        hits.append(
            {
                "sessionId": sid,
                "score": c.get("score", 0.0),
                "snippet": c.get("snippet", ""),
                "manifest": {
                    "id": sid,
                    "title": c.get("title", ""),
                    "tool": c.get("tool", ""),
                },
            }
        )
    return {"hits": hits, "answer": res.get("answer")}


@mcp.tool()
def session_summary(session_id: str) -> dict:
    """Structured summary of one session (verb session.summary, L1).

    Returns the hub's SummaryResponse {"summary", "generatedBy", "generatedAt"}.
    """
    return hub_request("GET", f"/v1/session/{session_id}/summary")


@mcp.tool()
def session_recent(session_id: str, n: int = 20) -> dict:
    """Last `n` normalized messages of a session (verb session.recent, L2).

    Returns the hub's RecentResponse {"messages": [NormalizedMessage, ...], "cursor"}.
    """
    return hub_request("GET", f"/v1/session/{session_id}/recent", params={"n": n})


@mcp.tool()
def session_grep(session_id: str, query: str, limit: int = 20) -> dict:
    """Keyword find-in-session (verb session.grep, L3).

    Returns the hub's GrepResponse {"matches": [GrepMatch, ...]}.
    """
    return hub_request(
        "GET", f"/v1/session/{session_id}/grep", params={"q": query, "limit": limit}
    )


@mcp.tool()
def session_handoff(
    session_id: str,
    levels: str = "summary,recent",
    n: int = 20,
) -> dict:
    """One-shot HandoffPacket — the full cross-agent handoff bundle (verb session.handoff).

    Returns the hub's HandoffPacket verbatim: the AICP envelope {protocol:"aicp/0.1",
    session, summary, recent, more:{grep,stream}, issuedAt, issuedBy, redacted} plus
    Freshet's additive keys {decisions, touchedFiles, workingSet, relatedSessions,
    openThreads, resumeHint}. `levels` is a comma list (summary,recent); `n` caps recent.
    """
    return hub_request(
        "GET",
        f"/v1/session/{session_id}/handoff",
        params={"levels": levels, "n": n},
    )


@mcp.tool()
def session_stream(session_id: str, from_cursor: Optional[str] = None) -> dict:
    """STUB (documented fast-follow) for verb session.stream (L4).

    Full client-side resumable streaming over MCP is not yet wired. The hub exposes a
    working SSE route at /v1/session/{id}/stream; this tool returns a pointer to it.
    """
    return {
        "note": (
            "session_stream is a documented fast-follow; consume the hub SSE route "
            "directly for now. Deep resumability (mid-flight reconnect dedupe, "
            "backpressure) is not yet implemented."
        ),
        "route": f"/v1/session/{session_id}/stream",
        "fromCursor": from_cursor,
    }


def main() -> None:
    """Run the stdio MCP server. stdout is the JSON-RPC channel — never print to it."""
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
