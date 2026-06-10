/**
 * Tests for branch-from-turn (Task 9).
 *
 * These tests exercise the pure logic of branchSession via an injected
 * filesystem so no Tauri runtime is required.
 */
import { describe, it, expect, vi } from "vitest";
import { parseClaude } from "./parsers/claude";
import {
  branchSession,
  rawLineIndexForMessage,
  type BranchFs,
} from "./branch";

const ORIGINAL_ID = "11111111-1111-1111-1111-111111111111";
const FILE_PATH = `/Users/alice/.claude/projects/-Users-alice-work-proj/${ORIGINAL_ID}.jsonl`;

/** A small claude JSONL fixture. Line indices (0-based):
 *  0: user  "Add retry logic"            → message m0
 *  1: assistant text + tool_use          → tool m1, assistant m2
 *  2: user  tool_result + text           → tool m3, user m4
 *  3: assistant text                     → assistant m5
 */
const FIXTURE_LINES = [
  JSON.stringify({
    type: "user",
    message: { role: "user", content: "Add retry logic" },
    timestamp: "2026-06-08T09:00:00Z",
    sessionId: ORIGINAL_ID,
    cwd: "/Users/alice/work/proj",
  }),
  JSON.stringify({
    type: "assistant",
    message: {
      role: "assistant",
      model: "claude-opus-4-8",
      id: "asst-1",
      content: [
        { type: "text", text: "I will add retry logic." },
        { type: "tool_use", id: "tu-1", name: "Read", input: { path: "s3.ts" } },
      ],
      usage: { input_tokens: 100, output_tokens: 20 },
    },
    timestamp: "2026-06-08T09:00:05Z",
    sessionId: ORIGINAL_ID,
  }),
  JSON.stringify({
    type: "user",
    message: {
      role: "user",
      content: [
        { type: "tool_result", tool_use_id: "tu-1", content: "export const x = 1;" },
        { type: "text", text: "Also add a test." },
      ],
    },
    timestamp: "2026-06-08T09:01:00Z",
    sessionId: ORIGINAL_ID,
  }),
  JSON.stringify({
    type: "assistant",
    message: {
      role: "assistant",
      model: "claude-opus-4-8",
      id: "asst-2",
      content: [{ type: "text", text: "Done — added retry and a test." }],
      usage: { input_tokens: 150, output_tokens: 40 },
    },
    timestamp: "2026-06-08T09:02:00Z",
    sessionId: ORIGINAL_ID,
  }),
];
const FIXTURE = FIXTURE_LINES.join("\n");

function makeFs(initial: Record<string, string>): BranchFs & {
  files: Record<string, string>;
} {
  const files: Record<string, string> = { ...initial };
  return {
    files,
    readText: vi.fn(async (p: string) => {
      if (!(p in files)) throw new Error(`no such file: ${p}`);
      return files[p];
    }),
    writeText: vi.fn(async (p: string, content: string) => {
      files[p] = content;
    }),
  };
}

describe("rawLineIndexForMessage", () => {
  const session = parseClaude(FIXTURE, FILE_PATH);

  it("maps the first user message to raw line 0", () => {
    const first = session.messages.find((m) => m.role === "user");
    expect(rawLineIndexForMessage(FIXTURE_LINES, first!.id)).toBe(0);
  });

  it("maps the assistant text message (m2) to raw line 1", () => {
    const asst = session.messages.find(
      (m) => m.role === "assistant" && m.text.includes("I will add retry"),
    );
    expect(rawLineIndexForMessage(FIXTURE_LINES, asst!.id)).toBe(1);
  });

  it("maps the second-line tool_result message to raw line 2", () => {
    const tr = session.messages.find(
      (m) => m.role === "tool" && m.text.includes("export const x"),
    );
    expect(rawLineIndexForMessage(FIXTURE_LINES, tr!.id)).toBe(2);
  });

  it("returns -1 for an unknown id", () => {
    expect(rawLineIndexForMessage(FIXTURE_LINES, "m999")).toBe(-1);
  });
});

describe("branchSession", () => {
  it("writes a new file with exactly the prefix lines up to and including the branch message", async () => {
    const session = parseClaude(FIXTURE, FILE_PATH);
    const fs = makeFs({ [FILE_PATH]: FIXTURE });
    // branch at the assistant text message on raw line 1 (m2)
    const branchMsg = session.messages.find(
      (m) => m.role === "assistant" && m.text.includes("I will add retry"),
    )!;

    const result = await branchSession(session, branchMsg.id, fs);

    const writtenLines = fs.files[result.newFilePath]
      .split("\n")
      .filter((l) => l.trim().length > 0);
    // raw lines 0 and 1 kept
    expect(writtenLines).toHaveLength(2);
  });

  it("rewrites sessionId on every kept line to the new uuid", async () => {
    const session = parseClaude(FIXTURE, FILE_PATH);
    const fs = makeFs({ [FILE_PATH]: FIXTURE });
    const last = session.messages[session.messages.length - 1];

    const result = await branchSession(session, last.id, fs);
    const lines = fs.files[result.newFilePath]
      .split("\n")
      .filter((l) => l.trim().length > 0)
      .map((l) => JSON.parse(l));

    expect(result.newSessionId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    for (const l of lines) {
      // every line that carried a sessionId now carries the new one
      if ("sessionId" in l && l.sessionId !== undefined) {
        expect(l.sessionId).toBe(result.newSessionId);
      }
    }
    // none retains the original id
    expect(JSON.stringify(lines)).not.toContain(ORIGINAL_ID);
  });

  it("writes to the same project dir with <newId>.jsonl", async () => {
    const session = parseClaude(FIXTURE, FILE_PATH);
    const fs = makeFs({ [FILE_PATH]: FIXTURE });
    const branchMsg = session.messages[0];

    const result = await branchSession(session, branchMsg.id, fs);
    expect(result.newFilePath).toBe(
      `/Users/alice/.claude/projects/-Users-alice-work-proj/${result.newSessionId}.jsonl`,
    );
  });

  it("never modifies the original file", async () => {
    const session = parseClaude(FIXTURE, FILE_PATH);
    const fs = makeFs({ [FILE_PATH]: FIXTURE });
    const branchMsg = session.messages[0];

    await branchSession(session, branchMsg.id, fs);
    expect(fs.files[FILE_PATH]).toBe(FIXTURE);
    expect(fs.writeText).not.toHaveBeenCalledWith(
      FILE_PATH,
      expect.anything(),
    );
  });

  it("sets parent_session_id and branch_point_message_id on the returned session", async () => {
    const session = parseClaude(FIXTURE, FILE_PATH);
    const fs = makeFs({ [FILE_PATH]: FIXTURE });
    const branchMsg = session.messages[0];

    const result = await branchSession(session, branchMsg.id, fs);
    expect(result.session.parentSessionId).toBe(ORIGINAL_ID);
    expect(result.session.branchPointMessageId).toBe(branchMsg.id);
    expect(result.session.id).toBe(result.newSessionId);
    expect(result.session.filePath).toBe(result.newFilePath);
  });

  it("rejects non-claude-code sessions", async () => {
    const session = parseClaude(FIXTURE, FILE_PATH);
    const fs = makeFs({ [FILE_PATH]: FIXTURE });
    const codexLike = { ...session, tool: "codex" as const };
    await expect(
      branchSession(codexLike, session.messages[0].id, fs),
    ).rejects.toThrow(/claude/i);
  });

  it("rejects an unknown message id", async () => {
    const session = parseClaude(FIXTURE, FILE_PATH);
    const fs = makeFs({ [FILE_PATH]: FIXTURE });
    await expect(
      branchSession(session, "m999", fs),
    ).rejects.toThrow(/message/i);
  });
});
