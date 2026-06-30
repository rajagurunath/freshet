"""Freshet AICP broker — a stdio MCP server proxying the Context Hub REST binding.

This package exposes the seven AICP §6 verbs as MCP tools (dot wire form rendered
to underscore tool names): session_list, session_search, session_summary,
session_recent, session_grep, session_stream, session_handoff. It is a thin httpx
proxy over the Freshet hub; all assembly logic lives in the hub (contexthub.handoff).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
