// The shared, normalized session contract. The wire types are GENERATED from
// the Pydantic models in apps/api/contexthub/models.py:
//   1. cd apps/api && python scripts/export_schema.py
//   2. cd apps/desktop && npm run gen:types
// This module re-exports the generated types (camelCase in-app; the api
// client converts to/from snake_case on the wire) and keeps desktop-only
// helpers local. Drift is impossible: tsc fails if contract.gen.ts is stale.

import type {
  Author as GenAuthor,
  Citation as GenCitation,
  IngestRequest as GenIngestRequest,
  Message as GenMessage,
  NormalizedSession as GenNormalizedSession,
  QueryResponse as GenQueryResponse,
  SessionLink as GenSessionLink,
  TokenCounts as GenTokenCounts,
} from "./contract.gen";

export { CONTRACT_VERSION } from "./contract.gen";

/** Make selected keys required and non-nullable (the desktop always sets them). */
type WithRequired<T, K extends keyof T> = Omit<T, K> & {
  [P in K]-?: NonNullable<T[P]>;
};

// ─── re-exported contract types ──────────────────────────────────────────────

export type Tool = GenNormalizedSession["tool"];
export type Role = GenMessage["role"];
export type Category = NonNullable<GenIngestRequest["category"]>;
export type Visibility = NonNullable<GenIngestRequest["visibility"]>;

export type Author = GenAuthor;
export type SessionLink = GenSessionLink;

/**
 * A session message, extended with desktop-side `kind` for special display.
 * `kind: "compact-marker"` marks the Claude Code post-compact continuation
 * message ("This session is being continued from a previous conversation…")
 * so the UI can render it as a visible compaction divider instead of a
 * regular user bubble.
 */
export type SessionMessage = GenMessage & {
  kind?: "compact-marker";
};

/** Parsers always set both counts (wire contract defaults them to 0). */
export type SessionTokenUsage = WithRequired<GenTokenCounts, "input" | "output">;

/** The server types `tool` as a plain string; the app narrows it to known tools. */
export type Citation = Omit<GenCitation, "tool"> & { tool: Tool };

export type QueryResponse = Omit<GenQueryResponse, "citations"> & {
  citations: Citation[];
};

/**
 * The normalized session, identical shape across all assistants.
 * Parsers always populate the list/scalar fields, so they are required here
 * even though the wire contract allows them to be omitted (server defaults).
 *
 * `messages` is overridden to use the desktop-extended `SessionMessage` type
 * so that compact-marker and other display-only `kind` values are available.
 */
export type NormalizedSession = Omit<
  WithRequired<
    GenNormalizedSession,
    "messageCount" | "messages" | "models" | "preview" | "filePath"
  >,
  "tokens" | "messages"
> & {
  tokens?: SessionTokenUsage;
  messages: SessionMessage[];
};

/** What the desktop app POSTs to /v1/sessions (snake_cased on the wire). */
export type PushEnvelope = Omit<
  WithRequired<GenIngestRequest, "category" | "visibility" | "redacted">,
  "session"
> & { session: NormalizedSession };

// ─── desktop-only helpers ────────────────────────────────────────────────────

export const TOOL_LABELS: Record<Tool, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  "kilo-code": "Kilo Code",
};

export const CATEGORIES: Category[] = [
  "engineering",
  "sales",
  "marketing",
  "research",
  "ops",
  "other",
];

/** Lightweight catalog entry for list views (no full messages). */
export type SessionSummary = Omit<NormalizedSession, "messages">;
