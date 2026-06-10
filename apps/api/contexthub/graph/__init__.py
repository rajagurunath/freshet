"""Knowledge graph / GraphRAG-lite (Task 13).

A lightweight SQLite-backed knowledge graph extracted from session summaries.

Modules
-------
store    SQLite store for nodes + edges (dedup by (kind, name), provenance via
         session_id, visibility carried from the source session).
extract  LLM-driven extraction of nodes/edges from a session summary/transcript.

The graph is *augmentation*, not a system of record: every node/edge keeps the
session_id it came from, and graph reads enforce the same visibility rules as
the session catalog.
"""
