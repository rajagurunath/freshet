/**
 * Parser unit tests – no Tauri dependency, pure parse-function calls.
 */
import { describe, it, expect } from "vitest";
import { parseClaude } from "../claude";
import { parseCodex } from "../codex";
import { parseKilo } from "../kilo";
import { detectTool } from "../index";

// ─── detectTool ──────────────────────────────────────────────────────────────

describe("detectTool", () => {
  it("detects claude-code from path", () => {
    expect(
      detectTool("/Users/alice/.claude/projects/-Users-alice-proj/abc123.jsonl")
    ).toBe("claude-code");
  });

  it("detects codex from path", () => {
    expect(
      detectTool("/Users/bob/.codex/sessions/2026/06/06/rollout-xxx.jsonl")
    ).toBe("codex");
  });

  it("detects kilo-code from path", () => {
    expect(
      detectTool(
        "/Users/carol/Library/Application Support/Code/User/globalStorage/kilocode.kilo-code/tasks/task-1/api_conversation_history.json"
      )
    ).toBe("kilo-code");
  });

  it("returns null for unrecognized paths", () => {
    expect(detectTool("/tmp/random-file.jsonl")).toBeNull();
  });
});

// ─── parseClaude ─────────────────────────────────────────────────────────────

const CLAUDE_FIXTURE = [
  // session_id line (type summary — should be ignored for messages)
  JSON.stringify({
    type: "summary",
    summary: "Fix the S3 retry bug",
    sessionId: "sess-abc-123",
  }),
  // user message with string content
  JSON.stringify({
    type: "user",
    message: { role: "user", content: "Can you fix the S3 retry logic?" },
    timestamp: "2026-06-08T09:00:00Z",
    sessionId: "sess-abc-123",
    cwd: "/Users/alice/work/data-pipeline",
  }),
  // assistant message with text + tool_use blocks
  JSON.stringify({
    type: "assistant",
    message: {
      role: "assistant",
      model: "claude-opus-4-8",
      id: "msg-1",
      content: [
        { type: "text", text: "I will fix the retry logic now." },
        { type: "tool_use", id: "tu-1", name: "Read", input: { path: "src/s3.ts" } },
      ],
      usage: { input_tokens: 1000, output_tokens: 200 },
    },
    timestamp: "2026-06-08T09:00:05Z",
  }),
  // user message with array content containing tool_result
  JSON.stringify({
    type: "user",
    message: {
      role: "user",
      content: [
        {
          type: "tool_result",
          tool_use_id: "tu-1",
          content: "export async function upload() {}",
        },
        { type: "text", text: "Also add a unit test." },
      ],
    },
    timestamp: "2026-06-08T09:01:00Z",
  }),
  // second assistant message with thinking
  JSON.stringify({
    type: "assistant",
    message: {
      role: "assistant",
      model: "claude-opus-4-8",
      id: "msg-2",
      content: [
        { type: "thinking", thinking: "The code is missing retry logic..." },
        { type: "text", text: "Here is the updated code with retry." },
      ],
      usage: { input_tokens: 1500, output_tokens: 400 },
    },
    timestamp: "2026-06-08T09:02:00Z",
  }),
].join("\n");

describe("parseClaude", () => {
  const session = parseClaude(
    CLAUDE_FIXTURE,
    "/Users/alice/.claude/projects/-Users-alice-work-data-pipeline/sess-abc-123.jsonl"
  );

  it("extracts the session id from the file basename", () => {
    expect(session.id).toBe("sess-abc-123");
  });

  it("sets tool to claude-code", () => {
    expect(session.tool).toBe("claude-code");
  });

  it("sets cwd from the user line", () => {
    expect(session.cwd).toBe("/Users/alice/work/data-pipeline");
  });

  it("derives project from cwd", () => {
    expect(session.project).toBe("data-pipeline");
  });

  it("sets startedAt from first timestamp", () => {
    expect(session.startedAt).toBe("2026-06-08T09:00:00Z");
  });

  it("sets endedAt from last timestamp", () => {
    expect(session.endedAt).toBe("2026-06-08T09:02:00Z");
  });

  it("sets title/preview from first user text", () => {
    expect(session.title).toBe("Can you fix the S3 retry logic?");
    expect(session.preview).toBe("Can you fix the S3 retry logic?");
  });

  it("emits tool messages for tool_use blocks", () => {
    const toolMsg = session.messages.find(
      (m) => m.role === "tool" && m.toolName === "Read"
    );
    expect(toolMsg).toBeDefined();
    expect(toolMsg?.text).toContain("Read");
  });

  it("emits tool messages for tool_result blocks", () => {
    const toolResultMsg = session.messages.find(
      (m) => m.role === "tool" && m.text.includes("export async function")
    );
    expect(toolResultMsg).toBeDefined();
  });

  it("emits user messages for text blocks in array content", () => {
    const userMsg = session.messages.find(
      (m) => m.role === "user" && m.text === "Also add a unit test."
    );
    expect(userMsg).toBeDefined();
  });

  it("captures thinking blocks on assistant messages", () => {
    const thinkingMsg = session.messages.find(
      (m) => m.role === "assistant" && m.thinking
    );
    expect(thinkingMsg?.thinking).toContain("retry logic");
  });

  it("accumulates token usage", () => {
    expect(session.tokens?.input).toBe(2500);
    expect(session.tokens?.output).toBe(600);
  });

  it("collects unique models", () => {
    expect(session.models).toEqual(["claude-opus-4-8"]);
  });

  it("has correct messageCount matching messages array length", () => {
    expect(session.messageCount).toBe(session.messages.length);
  });

  it("has at least 5 messages (user, tool, assistant, tool_result, user, assistant)", () => {
    expect(session.messages.length).toBeGreaterThanOrEqual(5);
  });
});

// Ensure system-reminder user messages are skipped for title
describe("parseClaude – system message skipping", () => {
  const fixture = [
    JSON.stringify({
      type: "user",
      message: {
        role: "user",
        content: "<system-reminder>\nYou are Claude...\n</system-reminder>",
      },
      timestamp: "2026-06-08T10:00:00Z",
    }),
    JSON.stringify({
      type: "user",
      message: { role: "user", content: "What is the meaning of life?" },
      timestamp: "2026-06-08T10:01:00Z",
    }),
  ].join("\n");

  it("skips system-reminder messages for title extraction", () => {
    const s = parseClaude(fixture, "/path/.claude/projects/-tmp/s1.jsonl");
    expect(s.title).toBe("What is the meaning of life?");
  });
});

// ─── parseCodex ──────────────────────────────────────────────────────────────

const CODEX_FIXTURE = [
  JSON.stringify({
    type: "session_meta",
    payload: {
      id: "codex-sess-xyz",
      timestamp: "2026-06-06T11:00:00Z",
      cwd: "/Users/bob/projects/ecommerce-api",
      cli_version: "1.2.3",
      originator: "cli",
    },
  }),
  // user message
  JSON.stringify({
    type: "response_item",
    payload: {
      type: "message",
      role: "user",
      content: [{ type: "input_text", text: "Add a composite index on user_id, created_at." }],
    },
  }),
  // reasoning (should attach as thinking to next assistant msg)
  JSON.stringify({
    type: "response_item",
    payload: {
      type: "reasoning",
      summary: [{ type: "summary_text", text: "The table needs an index for performance." }],
    },
  }),
  // assistant message
  JSON.stringify({
    type: "response_item",
    payload: {
      type: "message",
      role: "assistant",
      content: [{ type: "output_text", text: "I will create a migration file for the index." }],
    },
  }),
  // function call
  JSON.stringify({
    type: "response_item",
    payload: {
      type: "function_call",
      name: "shell",
      arguments: JSON.stringify({ command: "psql -c 'CREATE INDEX ...'" }),
    },
  }),
  // function call output
  JSON.stringify({
    type: "response_item",
    payload: {
      type: "function_call_output",
      output: "CREATE INDEX",
    },
  }),
].join("\n");

describe("parseCodex", () => {
  const session = parseCodex(
    CODEX_FIXTURE,
    "/Users/bob/.codex/sessions/2026/06/06/rollout-xxx-codex-sess-xyz.jsonl"
  );

  it("extracts session id from session_meta", () => {
    expect(session.id).toBe("codex-sess-xyz");
  });

  it("sets tool to codex", () => {
    expect(session.tool).toBe("codex");
  });

  it("sets cwd from session_meta", () => {
    expect(session.cwd).toBe("/Users/bob/projects/ecommerce-api");
  });

  it("sets startedAt from session_meta", () => {
    expect(session.startedAt).toBe("2026-06-06T11:00:00Z");
  });

  it("sets title/preview from first user message", () => {
    expect(session.title).toContain("composite index");
  });

  it("attaches reasoning as thinking on assistant message", () => {
    const assistantMsg = session.messages.find(
      (m) => m.role === "assistant" && m.thinking
    );
    expect(assistantMsg).toBeDefined();
    expect(assistantMsg?.thinking).toContain("index");
  });

  it("emits tool message for function_call", () => {
    const toolMsg = session.messages.find(
      (m) => m.role === "tool" && m.toolName === "shell"
    );
    expect(toolMsg).toBeDefined();
    expect(toolMsg?.text).toContain("shell");
  });

  it("emits tool message for function_call_output", () => {
    const outputMsg = session.messages.find(
      (m) => m.role === "tool" && m.text === "CREATE INDEX"
    );
    expect(outputMsg).toBeDefined();
  });

  it("has a user message with correct text", () => {
    const userMsg = session.messages.find((m) => m.role === "user");
    expect(userMsg?.text).toContain("composite index");
  });

  it("has correct messageCount", () => {
    expect(session.messageCount).toBe(session.messages.length);
  });
});

// ─── parseKilo ───────────────────────────────────────────────────────────────

const KILO_API_FIXTURE = JSON.stringify([
  {
    role: "user",
    content: "Please refactor the DataTable component.",
  },
  {
    role: "assistant",
    content: [
      { type: "text", text: "I will refactor DataTable to use TanStack Table." },
      {
        type: "tool_use",
        id: "tu-kilo-1",
        name: "read_file",
        input: { path: "src/DataTable.tsx" },
      },
    ],
  },
  {
    role: "user",
    content: [
      {
        type: "tool_result",
        tool_use_id: "tu-kilo-1",
        content: "export function DataTable() { return <table />; }",
      },
      { type: "text", text: "Also add sorting support." },
    ],
  },
  {
    role: "assistant",
    content: [{ type: "text", text: "Here is the refactored DataTable with sorting." }],
  },
]);

const KILO_UI_FIXTURE = JSON.stringify([
  { ts: 1717596000000, type: "say", say: "task", text: "Refactoring DataTable" },
  { ts: 1717599600000, type: "say", say: "completion_result", text: "Done" },
]);

describe("parseKilo", () => {
  const session = parseKilo(
    KILO_API_FIXTURE,
    "/path/globalStorage/kilocode.kilo-code/tasks/kilo-task-abc/api_conversation_history.json",
    "kilo-task-abc",
    KILO_UI_FIXTURE
  );

  it("uses taskId as session id", () => {
    expect(session.id).toBe("kilo-task-abc");
  });

  it("sets tool to kilo-code", () => {
    expect(session.tool).toBe("kilo-code");
  });

  it("sets startedAt from first ui_message timestamp", () => {
    expect(session.startedAt).toBe(new Date(1717596000000).toISOString());
  });

  it("sets endedAt from last ui_message timestamp", () => {
    expect(session.endedAt).toBe(new Date(1717599600000).toISOString());
  });

  it("sets title/preview from first user message", () => {
    expect(session.title).toContain("DataTable");
  });

  it("emits tool messages for tool_use blocks in assistant content", () => {
    const toolMsg = session.messages.find(
      (m) => m.role === "tool" && m.toolName === "read_file"
    );
    expect(toolMsg).toBeDefined();
    expect(toolMsg?.text).toContain("read_file");
  });

  it("emits tool messages for tool_result blocks in user content", () => {
    const toolResultMsg = session.messages.find(
      (m) => m.role === "tool" && m.text.includes("DataTable")
    );
    expect(toolResultMsg).toBeDefined();
  });

  it("emits user messages for text blocks in array user content", () => {
    const userMsg = session.messages.find(
      (m) => m.role === "user" && m.text.includes("sorting")
    );
    expect(userMsg).toBeDefined();
  });

  it("has all assistant messages as role assistant", () => {
    const assistants = session.messages.filter((m) => m.role === "assistant");
    expect(assistants.length).toBeGreaterThanOrEqual(2);
  });

  it("has correct messageCount matching messages array length", () => {
    expect(session.messageCount).toBe(session.messages.length);
  });
});
