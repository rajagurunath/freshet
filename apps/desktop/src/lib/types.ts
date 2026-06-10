// The shared, normalized session contract. This is the single source of truth
// that every parser produces and the central API ingest endpoint consumes.
// Keep in sync with apps/api/contexthub/models.py.

export type Tool = "claude-code" | "codex" | "kilo-code";

export const TOOL_LABELS: Record<Tool, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  "kilo-code": "Kilo Code",
};

export type Role = "user" | "assistant" | "system" | "tool";

export type Category =
  | "engineering"
  | "sales"
  | "marketing"
  | "research"
  | "ops"
  | "other";

export const CATEGORIES: Category[] = [
  "engineering",
  "sales",
  "marketing",
  "research",
  "ops",
  "other",
];

export type Visibility = "company" | "team" | "private";

export interface SessionMessage {
  id: string;
  role: Role;
  /** Flattened human-readable text content. */
  text: string;
  /** Optional model reasoning / thinking blocks. */
  thinking?: string;
  /** For tool_use / tool_result messages. */
  toolName?: string;
  /** ISO-8601 timestamp if available. */
  timestamp?: string;
  model?: string;
}

export interface SessionTokenUsage {
  input: number;
  output: number;
}

/** The normalized session, identical shape across all assistants. */
export interface NormalizedSession {
  id: string;
  tool: Tool;
  title: string;
  cwd?: string;
  project?: string;
  startedAt?: string;
  endedAt?: string;
  messageCount: number;
  messages: SessionMessage[];
  models: string[];
  tokens?: SessionTokenUsage;
  /** Short preview (first user prompt, truncated). */
  preview: string;
  /** Local source file path. */
  filePath: string;
}

/** Lightweight catalog entry for list views (no full messages). */
export type SessionSummary = Omit<NormalizedSession, "messages">;

export interface Author {
  id: string;
  email: string;
  name: string;
}

/** What the desktop app POSTs to /v1/sessions. */
export interface PushEnvelope {
  session: NormalizedSession;
  summary?: string;
  category: Category;
  visibility: Visibility;
  author: Author;
  redacted: boolean;
}

/** A citation returned by the company agent. */
export interface Citation {
  sessionId: string;
  title: string;
  tool: Tool;
  author?: string;
  snippet: string;
  score: number;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
}
