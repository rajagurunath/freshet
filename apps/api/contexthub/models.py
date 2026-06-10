"""Pydantic models — the shared data contract between desktop app and API.

All field names use snake_case to match the JSON wire format.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Core session contract (shared with desktop app)
# ---------------------------------------------------------------------------

class TokenCounts(BaseModel):
    input: int = 0
    output: int = 0


class Message(BaseModel):
    id: str
    role: Literal["user", "assistant", "system", "tool"]
    text: str
    thinking: Optional[str] = None
    tool_name: Optional[str] = None
    timestamp: Optional[str] = None
    model: Optional[str] = None


class SessionLink(BaseModel):
    """An external artifact linked to a session (PR, issue, doc, or another session)."""

    kind: Literal["pr", "issue", "doc", "session"]
    url: str
    label: Optional[str] = None


class NormalizedSession(BaseModel):
    id: str
    tool: Literal["claude-code", "codex", "kilo-code"]
    title: str
    cwd: Optional[str] = None
    project: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    message_count: int = 0
    models: list[str] = Field(default_factory=list)
    tokens: Optional[TokenCounts] = None
    preview: str = ""
    file_path: str = ""
    messages: list[Message] = Field(default_factory=list)
    # --- contract v2 fields ---
    schema_version: int = 2
    compacted: bool = False
    compact_summary: Optional[str] = None
    # Branch lineage (branch-from-turn): the session this one was forked from
    # and the message id at which the fork happened.
    parent_session_id: Optional[str] = None
    branch_point_message_id: Optional[str] = None
    links: list[SessionLink] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Push envelope (desktop → API)
# ---------------------------------------------------------------------------

class Author(BaseModel):
    id: str
    email: str
    name: str
    team: Optional[str] = None  # author's team (for team-scoped visibility)


CategoryLiteral = Literal["engineering", "sales", "marketing", "research", "ops", "other"]
VisibilityLiteral = Literal["company", "team", "private"]


class IngestRequest(BaseModel):
    session: NormalizedSession
    summary: Optional[str] = None
    category: CategoryLiteral = "engineering"
    visibility: VisibilityLiteral = "company"
    author: Author
    redacted: bool = False


class IngestResponse(BaseModel):
    session_id: str
    blob_uri: str
    chunks_indexed: int
    summary_used: bool
    skipped: bool = False          # True when re-ingest found identical content_hash
    created_at: Optional[str] = None  # ISO timestamp; preserved on skip
    updated_at: Optional[str] = None  # ISO timestamp; set on every write
    job_id: Optional[str] = None   # set when summarize=true; the enqueued job id


# ---------------------------------------------------------------------------
# Query / RAG
# ---------------------------------------------------------------------------

class QueryFilters(BaseModel):
    category: Optional[CategoryLiteral] = None
    tool: Optional[Literal["claude-code", "codex", "kilo-code"]] = None
    project: Optional[str] = None
    author: Optional[str] = None  # author.id


class QueryRequest(BaseModel):
    question: str
    filters: Optional[QueryFilters] = None
    top_k: int = 8
    # Search mode: hybrid (FTS + vector + RRF), vector-only, or keyword-only.
    mode: Literal["hybrid", "vector", "keyword"] = "hybrid"
    # Optional per-request LLM override (e.g. "claude-cli", "codex-cli").
    provider: Optional[str] = None
    model: Optional[str] = None


class Citation(BaseModel):
    session_id: str
    title: str
    tool: str
    author: Optional[str] = None  # author name for display
    snippet: str          # ~200-char excerpt
    score: float


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

class SummarizeRequest(BaseModel):
    session: NormalizedSession
    # Optional per-request LLM override (e.g. "claude-cli", "codex-cli").
    provider: Optional[str] = None
    model: Optional[str] = None


class SummarizeResponse(BaseModel):
    summary: str


# ---------------------------------------------------------------------------
# Catalog / stats
# ---------------------------------------------------------------------------

class SessionCatalogRow(BaseModel):
    id: str
    tool: str
    title: str
    category: str
    author: Optional[str] = None
    team: Optional[str] = None      # author's team (from Author.team)
    project: Optional[str] = None
    visibility: str
    message_count: int
    models: list[str] = Field(default_factory=list)
    preview: str = ""
    created_at: str
    updated_at: Optional[str] = None
    blob_uri: str
    summary: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0          # tokens_input + tokens_output (denormalised for sorting)


class SessionPage(BaseModel):
    """Paginated list of catalog rows."""

    items: list[SessionCatalogRow]
    total: int
    limit: int
    offset: int


class SessionDetail(BaseModel):
    """Full detail response for a single session (catalog row + raw blob)."""

    catalog: SessionCatalogRow
    raw: Optional[dict] = None


class StatsResponse(BaseModel):
    total_sessions: int
    total_chunks: int
    sessions_by_tool: dict[str, int]
    sessions_by_category: dict[str, int]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class Job(BaseModel):
    """A background job record returned by GET /v1/jobs/{id} etc."""

    id: str
    kind: str
    payload: dict = Field(default_factory=dict)
    status: Literal["queued", "running", "done", "error"]
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    scheduled_for: Optional[str] = None
