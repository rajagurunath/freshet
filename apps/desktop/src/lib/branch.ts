/**
 * Branch-from-turn (Task 9).
 *
 * Creates a new Claude Code session file that forks an existing one at a chosen
 * message. The new file contains the raw JSONL prefix up to and including the
 * line that produced the branch-point message, with every `sessionId` field
 * rewritten to a fresh UUID v4 so `claude --resume <newId>` opens a clean
 * lineage. The original JSONL is never modified.
 *
 * Claude Code only (v1): other tools store sessions in shapes that don't
 * resume from a single rewritten file, so the UI hides the action for them.
 */
import type { NormalizedSession } from "./types";
import { dirname, joinPath } from "./path-utils";
import { writeText as tauriWriteText, readText as tauriReadText } from "./tauri";

/**
 * Minimal filesystem surface branchSession needs. Injectable so the core logic
 * is testable without a Tauri runtime. Defaults to the real Tauri bridge.
 */
export interface BranchFs {
  readText(path: string): Promise<string>;
  writeText(path: string, content: string): Promise<void>;
}

const DEFAULT_FS: BranchFs = {
  readText: tauriReadText,
  writeText: tauriWriteText,
};

export interface BranchResult {
  /** The newly written file's absolute path. */
  newFilePath: string;
  /** The fresh session id (UUID v4) used throughout the new file. */
  newSessionId: string;
  /** The parsed session for the branch, with lineage fields populated. */
  session: NormalizedSession;
  /** The `claude --resume <id>` command the user can run. */
  resumeCommand: string;
}

/** RFC-4122 UUID v4. Uses crypto.randomUUID when available, else a fallback. */
export function newUuidV4(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  // Fallback (deterministic-enough for non-crypto contexts / older runtimes).
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ─── message-id → raw-line mapping ────────────────────────────────────────────

// The parser assigns message ids `m0, m1, …` in emission order, and a single
// raw `user`/`assistant` line can emit several messages (text, tool_use,
// tool_result, …). To branch at a given message we must find the raw line that
// produced it. We replay the parser's emission order here so the mapping stays
// in lock-step with claude.ts. If claude.ts changes its emission order, the
// branch.test.ts parity tests catch the drift.

interface RawBlock {
  type: string;
  text?: string;
}

/** Count how many messages claude.ts emits for a single raw line. */
function emittedMessageCount(raw: string): number {
  let parsed: { type?: string; message?: { content?: unknown } };
  try {
    parsed = JSON.parse(raw);
  } catch {
    return 0;
  }

  if (parsed.type === "user") {
    const content = parsed.message?.content;
    if (content === undefined || content === null) return 0;
    if (typeof content === "string") {
      // skipped system-like lines emit 0; everything else emits 1
      return isSystemLikeText(content) ? 0 : 1;
    }
    if (Array.isArray(content)) {
      let count = 0;
      let hasUserText = false;
      for (const block of content as RawBlock[]) {
        if (block.type === "text") {
          const t = block.text ?? "";
          if (isSystemLikeText(t)) continue;
          if (isCompactContinuationMarker(t)) {
            count += 1; // emitted as its own compact-marker message
          } else {
            hasUserText = true;
          }
        } else if (block.type === "tool_result") {
          count += 1;
        }
      }
      if (hasUserText) count += 1;
      return count;
    }
    return 0;
  }

  if (parsed.type === "assistant") {
    const msg = parsed.message as { content?: RawBlock[] } | undefined;
    if (!msg) return 0;
    let count = 0;
    let hasProse = false;
    for (const block of msg.content ?? []) {
      if (block.type === "text" || block.type === "thinking") {
        hasProse = true;
      } else if (block.type === "tool_use") {
        count += 1;
      }
    }
    if (hasProse) count += 1;
    return count;
  }

  // summary / other lines emit nothing
  return 0;
}

// Mirror of the two predicates in claude.ts (kept in sync via parity tests).
function isSystemLikeText(text: string): boolean {
  const t = text.trimStart();
  return (
    t.startsWith("<system-reminder>") ||
    t.startsWith("<command-name>") ||
    t.startsWith("<parameter name=") ||
    t.startsWith("You are ") ||
    t.startsWith("Task:")
  );
}

function isCompactContinuationMarker(text: string): boolean {
  return text
    .trimStart()
    .startsWith("This session is being continued from a previous conversation");
}

/**
 * Return the 0-based raw-line index that produced the message with the given
 * synthetic id (`m<idx>`), or -1 if not found. `rawLines` must be the
 * non-blank JSONL lines in file order (same filtering claude.ts applies).
 */
export function rawLineIndexForMessage(
  rawLines: string[],
  messageId: string,
): number {
  const m = /^m(\d+)$/.exec(messageId);
  if (!m) return -1;
  const target = Number(m[1]);

  let emitted = 0;
  for (let i = 0; i < rawLines.length; i++) {
    const n = emittedMessageCount(rawLines[i]);
    if (target < emitted + n) return i;
    emitted += n;
  }
  return -1;
}

// ─── branch ───────────────────────────────────────────────────────────────────

/** Rewrite a single raw JSONL line's `sessionId` field to `newId`. */
function rewriteSessionId(rawLine: string, newId: string): string {
  let obj: Record<string, unknown>;
  try {
    obj = JSON.parse(rawLine);
  } catch {
    return rawLine; // leave malformed lines untouched
  }
  if ("sessionId" in obj) obj.sessionId = newId;
  return JSON.stringify(obj);
}

/**
 * Branch a Claude Code session at `messageId`. Reads the original JSONL, keeps
 * the raw prefix up to and including the line that produced `messageId`,
 * rewrites every `sessionId` to a fresh UUID v4, and writes the result to a new
 * `<newId>.jsonl` in the same project directory. Returns the new file path,
 * id, and a parsed session carrying `parentSessionId`/`branchPointMessageId`.
 *
 * Throws on non-claude-code sessions or an unknown message id. Never writes the
 * original file.
 */
export async function branchSession(
  session: NormalizedSession,
  messageId: string,
  fs: BranchFs = DEFAULT_FS,
): Promise<BranchResult> {
  if (session.tool !== "claude-code") {
    throw new Error("Branching is supported for Claude Code sessions only.");
  }
  if (!session.filePath) {
    throw new Error("Cannot branch a session with no source file path.");
  }

  const raw = await fs.readText(session.filePath);
  const rawLines = raw.split("\n").filter((l) => l.trim().length > 0);

  const cutIndex = rawLineIndexForMessage(rawLines, messageId);
  if (cutIndex === -1) {
    throw new Error(`Branch message ${messageId} not found in session file.`);
  }

  const newSessionId = newUuidV4();
  const keptLines = rawLines
    .slice(0, cutIndex + 1)
    .map((l) => rewriteSessionId(l, newSessionId));

  const projectDir = dirname(session.filePath);
  const newFilePath = joinPath(projectDir, `${newSessionId}.jsonl`);

  if (newFilePath === session.filePath) {
    // Astronomically unlikely uuid collision — refuse rather than overwrite.
    throw new Error("New session id collided with the original; retry.");
  }

  await fs.writeText(newFilePath, keptLines.join("\n") + "\n");

  // Parse the new file so the caller gets a ready-to-display session with
  // lineage. Lazy import to avoid a cycle (parsers import types only).
  const { parseClaude } = await import("./parsers/claude");
  const parsed = parseClaude(keptLines.join("\n"), newFilePath);
  parsed.id = newSessionId;
  parsed.parentSessionId = session.id;
  parsed.branchPointMessageId = messageId;

  return {
    newFilePath,
    newSessionId,
    session: parsed,
    resumeCommand: `claude --resume ${newSessionId}`,
  };
}
