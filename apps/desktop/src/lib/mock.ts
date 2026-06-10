/**
 * Realistic demo sessions used in browser/demo mode (when Tauri is not available)
 * and in unit tests.
 */
import type { NormalizedSession } from "./types";

export const MOCK_SESSIONS: NormalizedSession[] = [
  // ── 1. Claude Code – engineering: S3 retry bug fix ──────────────────────
  {
    id: "sess-cc-001",
    tool: "claude-code",
    title: "Fix S3 upload retry logic with exponential backoff",
    cwd: "/Users/alice/work/data-pipeline",
    project: "data-pipeline",
    startedAt: "2026-06-08T09:14:22Z",
    endedAt: "2026-06-08T10:02:44Z",
    messageCount: 12,
    models: ["claude-opus-4-8"],
    tokens: { input: 42810, output: 8203 },
    preview:
      "The S3 upload is failing silently on transient 5xx errors. Can you add proper exponential backoff with jitter?",
    filePath: "/Users/alice/.claude/projects/-Users-alice-work-data-pipeline/sess-cc-001.jsonl",
    messages: [
      {
        id: "m0",
        role: "user",
        text: "The S3 upload is failing silently on transient 5xx errors. Can you add proper exponential backoff with jitter?",
        timestamp: "2026-06-08T09:14:22Z",
      },
      {
        id: "m1",
        role: "assistant",
        text: "I'll add exponential backoff with jitter to the S3 upload function. Let me first read the current implementation.",
        model: "claude-opus-4-8",
        timestamp: "2026-06-08T09:14:35Z",
      },
      {
        id: "m2",
        role: "tool",
        toolName: "Read",
        text: "Read(path)",
        timestamp: "2026-06-08T09:14:36Z",
      },
      {
        id: "m3",
        role: "tool",
        text: "async function uploadToS3(buf: Buffer, key: string) { await s3.putObject({Bucket, Key: key, Body: buf}).promise(); }",
        timestamp: "2026-06-08T09:14:37Z",
      },
      {
        id: "m4",
        role: "assistant",
        text: "I see the current implementation has **no retry logic**. Here's my plan:\n\n1. Wrap `putObject` in a retry loop\n2. Use exponential backoff with full jitter\n3. Only retry on transient 5xx, cap at 5 attempts\n\n```ts\nasync function uploadToS3(buf: Buffer, key: string) {\n  const maxAttempts = 5;\n  for (let attempt = 0; attempt < maxAttempts; attempt++) {\n    try {\n      return await s3.putObject({ Bucket, Key: key, Body: buf }).promise();\n    } catch (err) {\n      if (!isTransient(err) || attempt === maxAttempts - 1) throw err;\n      const backoff = Math.random() * 2 ** attempt * 100; // full jitter\n      await sleep(backoff);\n    }\n  }\n}\n```\n\nThis avoids a *thundering herd* by jittering each retry.",
        model: "claude-opus-4-8",
        timestamp: "2026-06-08T09:15:10Z",
      },
      {
        id: "m5",
        role: "tool",
        toolName: "Edit",
        text: "Edit(path, old_string, new_string)",
        timestamp: "2026-06-08T09:15:11Z",
      },
      {
        id: "m6",
        role: "user",
        text: "Looks good! Can you also add a test for the retry logic?",
        timestamp: "2026-06-08T09:30:00Z",
      },
      {
        id: "m7",
        role: "assistant",
        text: "Sure. Here's a vitest test that mocks S3 to throw a `503` twice, then succeed:\n\n```ts\nit('retries transient 5xx then succeeds', async () => {\n  const put = vi.fn()\n    .mockRejectedValueOnce(new S3Error(503))\n    .mockRejectedValueOnce(new S3Error(503))\n    .mockResolvedValueOnce({ ETag: 'abc' });\n  s3.putObject = () => ({ promise: put });\n\n  const res = await uploadToS3(Buffer.from('x'), 'key');\n  expect(res.ETag).toBe('abc');\n  expect(put).toHaveBeenCalledTimes(3);\n});\n```\n\nCoverage summary:\n\n| Scenario | Attempts | Result |\n|----------|----------|--------|\n| 2× 503 then 200 | 3 | pass |\n| Persistent 500 | 5 | throws |",
        model: "claude-opus-4-8",
        timestamp: "2026-06-08T09:30:15Z",
      },
    ],
  },

  // ── 2. Claude Code – engineering: TypeScript migration ──────────────────
  {
    id: "sess-cc-002",
    tool: "claude-code",
    title: "Migrate auth module from JavaScript to TypeScript",
    cwd: "/Users/alice/work/auth-service",
    project: "auth-service",
    startedAt: "2026-06-07T14:05:00Z",
    endedAt: "2026-06-07T15:45:30Z",
    messageCount: 18,
    models: ["claude-sonnet-4-6"],
    tokens: { input: 68200, output: 12450 },
    preview:
      "I need to migrate src/auth/index.js and its helpers to TypeScript. Start with the type definitions.",
    filePath: "/Users/alice/.claude/projects/-Users-alice-work-auth-service/sess-cc-002.jsonl",
    messages: [
      {
        id: "m0",
        role: "user",
        text: "I need to migrate src/auth/index.js and its helpers to TypeScript. Start with the type definitions.",
        timestamp: "2026-06-07T14:05:00Z",
      },
      {
        id: "m1",
        role: "assistant",
        text: "I'll start by reading the existing JS files to understand the current API surface.",
        model: "claude-sonnet-4-6",
        timestamp: "2026-06-07T14:05:18Z",
      },
      {
        id: "m2",
        role: "tool",
        toolName: "Bash",
        text: "Bash(command)",
        timestamp: "2026-06-07T14:05:19Z",
      },
      {
        id: "m3",
        role: "user",
        text: "Also make sure the JWT payload type is strict — no any.",
        timestamp: "2026-06-07T14:45:00Z",
      },
      {
        id: "m4",
        role: "assistant",
        text: "Absolutely. I'll define a JwtPayload interface with all known fields typed correctly.",
        model: "claude-sonnet-4-6",
        timestamp: "2026-06-07T14:45:20Z",
      },
    ],
  },

  // ── 3. Codex – engineering: database index optimization ──────────────────
  {
    id: "codex-rollout-2026-06-06-abc123",
    tool: "codex",
    title: "Add composite index on (user_id, created_at) in orders table",
    cwd: "/Users/bob/projects/ecommerce-api",
    project: "ecommerce-api",
    startedAt: "2026-06-06T11:00:00Z",
    endedAt: undefined,
    messageCount: 10,
    models: [],
    tokens: undefined,
    preview:
      "The orders query is doing a full table scan. We need a composite index on user_id and created_at.",
    filePath: "/Users/bob/.codex/sessions/2026/06/06/rollout-1717668000-abc123.jsonl",
    messages: [
      {
        id: "m0",
        role: "user",
        text: "The orders query is doing a full table scan. We need a composite index on user_id and created_at.",
      },
      {
        id: "m1",
        role: "assistant",
        text: "Let me check the current schema and query plans before adding the index.",
      },
      {
        id: "m2",
        role: "tool",
        toolName: "shell",
        text: "shell(command)",
      },
      {
        id: "m3",
        role: "tool",
        text: "EXPLAIN ANALYZE output: Seq Scan on orders (cost=0.00..45231.00 rows=1500000)",
      },
      {
        id: "m4",
        role: "assistant",
        text: "The sequential scan confirms the issue. Here's the migration to add the composite index.",
      },
    ],
  },

  // ── 4. Kilo Code – engineering: React component refactor ─────────────────
  {
    id: "kilo-task-20260605-refactor",
    tool: "kilo-code",
    title: "Refactor DataTable to use TanStack Table v8",
    cwd: "/Users/carol/projects/dashboard",
    project: "dashboard",
    startedAt: "2026-06-05T16:20:00Z",
    endedAt: "2026-06-05T17:55:00Z",
    messageCount: 9,
    models: [],
    tokens: undefined,
    preview:
      "Please refactor the DataTable component from our custom table impl to TanStack Table v8.",
    filePath:
      "/Users/carol/Library/Application Support/Code/User/globalStorage/kilocode.kilo-code/tasks/kilo-task-20260605-refactor/api_conversation_history.json",
    messages: [
      {
        id: "m0",
        role: "user",
        text: "Please refactor the DataTable component from our custom table impl to TanStack Table v8.",
        timestamp: "2026-06-05T16:20:00Z",
      },
      {
        id: "m1",
        role: "assistant",
        text: "I'll start by reading the current DataTable implementation to understand what needs to change.",
        timestamp: "2026-06-05T16:20:30Z",
      },
      {
        id: "m2",
        role: "tool",
        toolName: "read_file",
        text: "read_file(path)",
      },
      {
        id: "m3",
        role: "user",
        text: "Also make sure column sorting and pagination state are URL-synced.",
        timestamp: "2026-06-05T17:00:00Z",
      },
    ],
  },

  // ── 5. Claude Code – sales: discovery call prep ──────────────────────────
  {
    id: "sess-cc-sales-001",
    tool: "claude-code",
    title: "Draft ACME Corp discovery call script and pain-point map",
    cwd: "/Users/david/sales/acme-discovery",
    project: "acme-discovery",
    startedAt: "2026-06-04T08:30:00Z",
    endedAt: "2026-06-04T09:15:00Z",
    messageCount: 8,
    models: ["claude-opus-4-8"],
    tokens: { input: 15200, output: 4300 },
    preview:
      "I have a discovery call with ACME Corp tomorrow. They're a 500-person logistics company struggling with manual reporting. Help me draft a call script.",
    filePath: "/Users/david/.claude/projects/-Users-david-sales-acme-discovery/sess-cc-sales-001.jsonl",
    messages: [
      {
        id: "m0",
        role: "user",
        text: "I have a discovery call with ACME Corp tomorrow. They're a 500-person logistics company struggling with manual reporting. Help me draft a call script.",
        timestamp: "2026-06-04T08:30:00Z",
      },
      {
        id: "m1",
        role: "assistant",
        text: "I'll draft a structured discovery call script that uncovers their reporting pain points and maps them to our platform's strengths.",
        model: "claude-opus-4-8",
        timestamp: "2026-06-04T08:30:20Z",
      },
      {
        id: "m2",
        role: "user",
        text: "Good. Also add a section on budget qualification and timeline.",
        timestamp: "2026-06-04T08:55:00Z",
      },
      {
        id: "m3",
        role: "assistant",
        text: "Added MEDDIC-style budget and timeline qualification questions to section 4.",
        model: "claude-opus-4-8",
        timestamp: "2026-06-04T08:55:30Z",
      },
    ],
  },

  // ── 6. Codex – marketing: blog post drafting ────────────────────────────
  {
    id: "codex-rollout-2026-06-03-mktg",
    tool: "codex",
    title: "Draft blog post: 5 ways AI coding assistants boost team velocity",
    cwd: "/Users/eve/marketing/content",
    project: "content",
    startedAt: "2026-06-03T10:00:00Z",
    endedAt: undefined,
    messageCount: 7,
    models: [],
    tokens: undefined,
    preview:
      "Write a 1000-word blog post targeting engineering managers on how AI coding assistants improve sprint velocity.",
    filePath: "/Users/eve/.codex/sessions/2026/06/03/rollout-1717574400-mktg.jsonl",
    messages: [
      {
        id: "m0",
        role: "user",
        text: "Write a 1000-word blog post targeting engineering managers on how AI coding assistants improve sprint velocity.",
      },
      {
        id: "m1",
        role: "assistant",
        text: "Here's a draft focused on five concrete metrics: PR cycle time, context-switch reduction, onboarding speed, test coverage, and unblocking rate.",
      },
      {
        id: "m2",
        role: "user",
        text: "Add a section on ROI calculation with a sample formula.",
      },
      {
        id: "m3",
        role: "assistant",
        text: "Added an ROI section with a formula: (hours_saved × eng_hourly_rate − tool_cost) / tool_cost × 100.",
      },
    ],
  },
];
