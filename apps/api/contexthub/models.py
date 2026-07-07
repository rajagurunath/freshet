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
    # "pending" when the session is held for review (REVIEW_REQUIRED=true and
    # visibility != private); None when it was integrated immediately.
    review_status: Optional[Literal["pending", "approved", "rejected"]] = None


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
    # When true, augment retrieval with knowledge-graph context (Task 13):
    # match question terms against node names, pull 1-hop neighbors, and append
    # a "Knowledge graph context" block to the LLM context.
    use_graph: bool = False


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
# Batch summarization
# ---------------------------------------------------------------------------

BatchProviderLiteral = Literal["openai-batch", "local", "default"]


class BatchSummarizeRequest(BaseModel):
    """Request body for POST /v1/summarize/batch."""

    session_ids: list[str] = Field(..., min_length=1)
    provider: BatchProviderLiteral = "default"
    model: Optional[str] = None   # optional model override (used for openai-batch / local)


class BatchSummarizeResponse(BaseModel):
    """Response for POST /v1/summarize/batch."""

    job_id: str
    kind: str = "summarize_batch"
    session_count: int


# ---------------------------------------------------------------------------
# Knowledge graph (Task 13)
# ---------------------------------------------------------------------------

GraphNodeKind = Literal["repo", "service", "feature", "person", "decision", "tool", "pr", "problem"]


class GraphNode(BaseModel):
    """A knowledge-graph node (deduped by (kind, name))."""

    id: str
    kind: str
    name: str
    summary: Optional[str] = None
    visibility: Optional[str] = None
    generic: bool = False
    session_ids: list[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed relation between two graph nodes."""

    id: str
    src: str
    dst: str
    rel: str
    weight: float = 1.0
    session_id: Optional[str] = None


class GraphResponse(BaseModel):
    """Response for GET /v1/graph and GET /v1/graph/session/{id}."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]


class GraphNodePatch(BaseModel):
    """PATCH /v1/graph/nodes/{id} — any subset of fields."""

    name: Optional[str] = None
    kind: Optional[str] = None
    summary: Optional[str] = None


class GraphNodeCreate(BaseModel):
    """POST /v1/graph/nodes — manual (human) node."""

    kind: str
    name: str
    summary: Optional[str] = None


class GraphEdgeCreate(BaseModel):
    """POST /v1/graph/edges — manual (human) edge between existing nodes."""

    src: str
    dst: str
    rel: str


# ---------------------------------------------------------------------------
# AICP — AI Context Protocol (session exchange, §6 of the spec)
# camelCase on the wire; snake_case in Python via alias generator.
# ---------------------------------------------------------------------------

from pydantic import ConfigDict
from pydantic.alias_generators import to_camel


class AICPModel(BaseModel):
    """Base for all AICP wire models: serialize/parse in camelCase."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class NormalizedMessage(AICPModel):
    """Wire form of :class:`Message` (camelCase). One transcript message."""

    id: str
    role: Literal["user", "assistant", "system", "tool"]
    text: str
    thinking: Optional[str] = None
    tool_name: Optional[str] = None       # -> toolName
    timestamp: Optional[str] = None
    model: Optional[str] = None


class SessionManifest(AICPModel):
    """L0 manifest — lightweight, no message bodies (spec §6)."""

    id: str
    tool: str                              # claude-code | codex | kilo-code
    title: str
    project: Optional[str] = None
    started_at: Optional[str] = None       # -> startedAt
    ended_at: Optional[str] = None         # -> endedAt
    message_count: int = 0                 # -> messageCount
    tokens: Optional[TokenCounts] = None
    has_summary: bool = False              # -> hasSummary
    visibility: str = "private"            # company | team | private
    source: str = "hub"                    # local | hub


class SummaryResponse(AICPModel):
    """session.summary (L1)."""

    summary: str
    generated_by: str                      # -> generatedBy
    generated_at: Optional[str] = None     # -> generatedAt


class RecentResponse(AICPModel):
    """session.recent (L2)."""

    messages: list[NormalizedMessage] = Field(default_factory=list)
    cursor: Optional[str] = None           # opaque base64 of the last message index


class GrepMatch(AICPModel):
    message_id: str                        # -> messageId
    offset: int
    role: str
    snippet: str


class GrepResponse(AICPModel):
    """session.grep (L3)."""

    matches: list[GrepMatch] = Field(default_factory=list)


class SearchHit(AICPModel):
    session_id: str                        # -> sessionId
    score: float
    snippet: str
    manifest: Optional[SessionManifest] = None


class SearchResponse(AICPModel):
    """session.search (L0/L3)."""

    hits: list[SearchHit] = Field(default_factory=list)
    answer: Optional[str] = None           # Freshet extension: the RAG answer


# --- handoff envelope + Freshet extension keys ---

class HandoffMore(AICPModel):
    grep: str = "session.grep"
    stream: str = "session.stream"


class HandoffDecision(AICPModel):
    decision: str
    why: Optional[str] = None


class HandoffWorkingSet(AICPModel):
    repos: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)


class HandoffRelatedSession(AICPModel):
    id: str
    title: Optional[str] = None
    why: Optional[str] = None


class HandoffPacket(AICPModel):
    """session.handoff (push). Spec envelope + Freshet superset keys."""

    # --- AICP envelope (spec §6, exact) ---
    protocol: Literal["aicp/0.1"] = "aicp/0.1"
    session: SessionManifest
    summary: str = ""
    recent: list[NormalizedMessage] = Field(default_factory=list)
    more: HandoffMore = Field(default_factory=HandoffMore)
    issued_at: str                         # -> issuedAt   (ISO8601, now)
    issued_by: str                         # -> issuedBy   ("freshet-local")
    redacted: bool = True
    # --- Freshet extension keys (ADDITIVE; a superset, not a replacement) ---
    decisions: list[HandoffDecision] = Field(default_factory=list)
    touched_files: list[str] = Field(default_factory=list)     # -> touchedFiles
    working_set: HandoffWorkingSet = Field(default_factory=HandoffWorkingSet)  # -> workingSet
    related_sessions: list[HandoffRelatedSession] = Field(default_factory=list)  # -> relatedSessions
    open_threads: list[str] = Field(default_factory=list)      # -> openThreads
    resume_hint: str = ""                  # -> resumeHint


# ---------------------------------------------------------------------------
# Rules (Task 14)
# ---------------------------------------------------------------------------

RuleStatusLiteral = Literal["proposed", "accepted", "rejected"]


class Rule(BaseModel):
    """A single extracted rule/preference."""

    id: str
    text: str
    rationale: Optional[str] = None
    evidence: list[str] = Field(default_factory=list)
    scope: Optional[str] = None
    status: RuleStatusLiteral = "proposed"
    author: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class RulePage(BaseModel):
    """Paginated list of rules."""

    items: list[Rule]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Reviews (PR-merge-style session approval)
# ---------------------------------------------------------------------------

ReviewStatusLiteral = Literal["pending", "approved", "rejected"]
ReviewVerdictLiteral = Literal["approve", "reject"]


class ReviewVote(BaseModel):
    """A single reviewer vote on a pending session."""

    id: str
    session_id: str
    reviewer_id: str
    reviewer_name: Optional[str] = None
    verdict: ReviewVerdictLiteral
    comment: Optional[str] = None
    created_at: str


class Review(BaseModel):
    """A review request for a pushed session awaiting integration."""

    session_id: str
    author_id: str
    author_name: Optional[str] = None
    title: str
    category: str
    visibility: str
    summary: Optional[str] = None
    status: ReviewStatusLiteral = "pending"
    approvals_required: int = 1
    approve_count: int = 0
    reject_count: int = 0
    my_vote: Optional[ReviewVerdictLiteral] = None
    votes: list[ReviewVote] = Field(default_factory=list)
    created_at: str
    updated_at: Optional[str] = None
    decided_at: Optional[str] = None


class ReviewPage(BaseModel):
    """Paginated list of review requests."""

    items: list[Review]
    total: int
    limit: int
    offset: int


class ReviewDetail(BaseModel):
    """Full review detail: request + votes + transcript preview from the blob."""

    review: Review
    preview: str = ""
    messages: list[Message] = Field(default_factory=list)


class ReviewVoteRequest(BaseModel):
    """POST /v1/reviews/{session_id}/vote body."""

    verdict: ReviewVerdictLiteral
    comment: Optional[str] = None


class ReviewStats(BaseModel):
    """GET /v1/reviews/stats — counts for the desktop badge."""

    pending: int = 0
    approved: int = 0
    rejected: int = 0


# ---------------------------------------------------------------------------
# Assets (Task 15)
# ---------------------------------------------------------------------------

AssetKindLiteral = Literal["skill", "script", "config", "prompt"]


class AssetMetadata(BaseModel):
    """Response model for an uploaded or listed asset."""

    id: str
    kind: str
    name: str
    description: str = ""
    category: str = "general"
    author: str
    team: Optional[str] = None
    visibility: str = "company"
    files: list[str] = Field(default_factory=list)
    blob_uri: str
    version: str = "1.0.0"
    created_at: str


class AssetPage(BaseModel):
    """Paginated list of asset metadata rows."""

    items: list[AssetMetadata]
    total: int
    limit: int
    offset: int


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
