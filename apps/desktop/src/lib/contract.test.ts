/**
 * Contract parity tests for the generated TypeScript contract.
 *
 * `contract.gen.ts` is generated from `apps/api/schema/contract.json` by
 * `npm run gen:types` (which in turn is exported from the Pydantic models by
 * `apps/api/scripts/export_schema.py`). These tests type-check a sample
 * fixture against the generated types — if the Python models change and the
 * generated file is stale, `tsc --noEmit` fails here.
 */
import { describe, expect, it } from "vitest";

import { CONTRACT_VERSION } from "./contract.gen";
import type {
  IngestRequest,
  IngestResponse,
  NormalizedSession as WireSession,
  QueryRequest,
  QueryResponse as WireQueryResponse,
  SessionLink,
  SummarizeResponse,
} from "./contract.gen";
import type { NormalizedSession, PushEnvelope } from "./types";

// ─── fixtures (compile-time checks) ──────────────────────────────────────────

const link: SessionLink = {
  kind: "pr",
  url: "https://github.com/acme/repo/pull/42",
  label: "PR #42",
};

const session: NormalizedSession = {
  id: "sess-1",
  tool: "claude-code",
  title: "Fix retry logic",
  cwd: "/Users/dev/acme",
  project: "acme",
  startedAt: "2026-06-10T09:00:00Z",
  messageCount: 2,
  models: ["claude-sonnet-4-6"],
  tokens: { input: 100, output: 50 },
  preview: "Fix the retry logic",
  filePath: "/tmp/sess-1.jsonl",
  messages: [
    { id: "m1", role: "user", text: "Fix the retry logic" },
    { id: "m2", role: "assistant", text: "Done.", model: "claude-sonnet-4-6" },
  ],
  // v2 contract fields
  schemaVersion: 2,
  compacted: true,
  compactSummary: "Earlier context was compacted.",
  parentSessionId: "sess-0",
  branchPointMessageId: "m9",
  links: [link],
};

const envelope: PushEnvelope = {
  session,
  summary: "Fixed retry logic with backoff.",
  category: "engineering",
  visibility: "company",
  author: { id: "u1", email: "dev@acme.com", name: "Dev" },
  redacted: true,
};

// The strict app-side session must remain assignable to the generated wire
// type — this is the drift guard between types.ts and contract.gen.ts.
const wireSession: WireSession = session;
const wireEnvelope: IngestRequest = envelope;

const queryRequest: QueryRequest = {
  question: "How did we fix retries?",
  topK: 8,
  filters: { project: "acme", tool: "claude-code" },
};

const queryResponse: WireQueryResponse = {
  answer: "With exponential backoff.",
  citations: [
    {
      sessionId: "sess-1",
      title: "Fix retry logic",
      tool: "claude-code",
      snippet: "added backoff",
      score: 0.92,
    },
  ],
};

const ingestResponse: IngestResponse = {
  sessionId: "sess-1",
  blobUri: "blob://sess-1",
  chunksIndexed: 3,
  summaryUsed: true,
};

const summarizeResponse: SummarizeResponse = { summary: "A summary." };

// ─── runtime assertions ──────────────────────────────────────────────────────

describe("generated contract", () => {
  it("is at contract version 2", () => {
    expect(CONTRACT_VERSION).toBe(2);
  });

  it("carries the v2 session fields", () => {
    expect(wireSession.schemaVersion).toBe(2);
    expect(wireSession.compacted).toBe(true);
    expect(wireSession.compactSummary).toContain("compacted");
    expect(wireSession.parentSessionId).toBe("sess-0");
    expect(wireSession.branchPointMessageId).toBe("m9");
    expect(wireSession.links?.[0]).toEqual(link);
  });

  it("envelope fixture round-trips through the wire type", () => {
    expect(wireEnvelope.session.id).toBe("sess-1");
    expect(wireEnvelope.redacted).toBe(true);
    expect(queryRequest.question.length).toBeGreaterThan(0);
    expect(queryResponse.citations[0].sessionId).toBe("sess-1");
    expect(ingestResponse.chunksIndexed).toBe(3);
    expect(summarizeResponse.summary).toBe("A summary.");
  });
});
