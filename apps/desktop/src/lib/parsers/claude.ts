/**
 * Parser for Claude Code JSONL session files.
 * Path pattern: ~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl
 */
import type { NormalizedSession, SessionMessage, SessionTokenUsage } from "../types";
import { basename, extname } from "../path-utils";

// ─── raw line shapes ────────────────────────────────────────────────────────

interface RawContentText {
  type: "text";
  text: string;
}
interface RawContentThinking {
  type: "thinking";
  thinking: string;
}
interface RawContentToolUse {
  type: "tool_use";
  id?: string;
  name: string;
  input?: Record<string, unknown>;
}
interface RawContentToolResult {
  type: "tool_result";
  tool_use_id?: string;
  content?: string | Array<{ type: string; text?: string }>;
}

type RawContentBlock =
  | RawContentText
  | RawContentThinking
  | RawContentToolUse
  | RawContentToolResult;

interface RawUsage {
  input_tokens?: number;
  output_tokens?: number;
  cache_read_input_tokens?: number;
  cache_creation_input_tokens?: number;
}

interface RawUserLine {
  type: "user";
  message: {
    role: "user";
    content: string | RawContentBlock[];
  };
  timestamp?: string;
  sessionId?: string;
  cwd?: string;
  /** Set by Claude Code on the post-/compact continuation message; its text IS the compact summary. */
  isCompactSummary?: boolean;
}

interface RawAssistantLine {
  type: "assistant";
  message: {
    role: "assistant";
    model?: string;
    id?: string;
    content: RawContentBlock[];
    usage?: RawUsage;
  };
  timestamp?: string;
}

interface RawSummaryLine {
  type: "summary";
  summary: string;
  sessionId?: string;
}

type RawLine = RawUserLine | RawAssistantLine | RawSummaryLine | { type: string; [k: string]: unknown };

// ─── helpers ────────────────────────────────────────────────────────────────

/**
 * Derive the cwd from a Claude projects dir-name (slashes replaced by dashes).
 * e.g. "-Users-alice-proj" → "/Users/alice/proj"
 */
function decodeCwd(dirSegment: string): string {
  // strip leading dash that represents the leading slash
  return dirSegment.replace(/^-/, "/").replace(/-/g, "/");
}

function fileBasenameNoExt(filePath: string): string {
  const b = basename(filePath);
  const e = extname(b);
  return e ? b.slice(0, -e.length) : b;
}

/**
 * True when a user message is likely a hook/system block that should be skipped
 * for title/preview extraction.
 */
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

/**
 * True when a user message is the post-compact continuation marker that
 * Claude Code emits after a /compact operation.
 */
function isCompactContinuationMarker(text: string): boolean {
  return text.trimStart().startsWith(
    "This session is being continued from a previous conversation"
  );
}

function extractUserText(content: string | RawContentBlock[]): string {
  if (typeof content === "string") return content;
  const parts: string[] = [];
  for (const block of content) {
    if (block.type === "text") parts.push((block as RawContentText).text);
  }
  return parts.join("\n");
}

// ─── main parser ─────────────────────────────────────────────────────────────

export function parseClaude(text: string, filePath: string): NormalizedSession {
  const lines = text.split("\n").filter((l) => l.trim().length > 0);

  let sessionId = fileBasenameNoExt(filePath);
  let cwd: string | undefined;
  let startedAt: string | undefined;
  let endedAt: string | undefined;
  const messages: SessionMessage[] = [];
  const modelSet = new Set<string>();
  let totalInput = 0;
  let totalOutput = 0;
  let msgIdx = 0;
  let compacted = false;
  let compactSummary: string | undefined;

  // Try to derive cwd from dir name
  const parts = filePath.replace(/\\/g, "/").split("/");
  // structure: .../.claude/projects/<encoded-cwd>/<file>.jsonl
  const projectsIdx = parts.lastIndexOf("projects");
  if (projectsIdx !== -1 && projectsIdx + 1 < parts.length - 1) {
    cwd = decodeCwd(parts[projectsIdx + 1]);
  }

  for (const rawLine of lines) {
    let parsed: RawLine;
    try {
      parsed = JSON.parse(rawLine) as RawLine;
    } catch {
      continue;
    }

    if (parsed.type === "summary") {
      // /compact output: capture the summary and mark the session as compacted.
      // Last summary line wins (multiple compactions in one file are rare but
      // handled by keeping the most recent).
      const summaryLine = parsed as RawSummaryLine;
      compacted = true;
      compactSummary = summaryLine.summary;
      if (summaryLine.sessionId) sessionId = summaryLine.sessionId;
    } else if (
      parsed.type === "system" &&
      (parsed as { subtype?: string }).subtype === "compact_boundary"
    ) {
      // Real-world /compact marker: Claude Code emits a system line with
      // subtype "compact_boundary" (and compactMetadata) at the compaction
      // point. (`type:"summary"` lines, handled above, are not what /compact
      // writes — kept only for robustness.)
      compacted = true;
      const sysLine = parsed as { timestamp?: string; sessionId?: string };
      if (sysLine.sessionId) sessionId = sysLine.sessionId;
      messages.push({
        id: `m${msgIdx++}`,
        role: "system",
        text: "Conversation compacted",
        timestamp: sysLine.timestamp,
        kind: "compact-marker",
      });
    } else if (parsed.type === "user") {
      const line = parsed as RawUserLine;
      const ts = line.timestamp;
      if (ts) {
        if (!startedAt) startedAt = ts;
        endedAt = ts;
      }
      if (line.sessionId) sessionId = line.sessionId;
      if (line.cwd) cwd = line.cwd;

      // flatten content
      const content = line.message?.content;
      if (!content) continue;

      // Claude Code flags the post-/compact continuation message with a
      // top-level isCompactSummary: true; its text is the compact summary.
      // The text heuristic remains as a fallback for older versions.
      const lineIsCompact = line.isCompactSummary === true;

      if (typeof content === "string") {
        if (!lineIsCompact && isSystemLikeText(content)) continue;
        // Detect the post-compact continuation marker.
        if (lineIsCompact || isCompactContinuationMarker(content)) {
          compacted = true;
          compactSummary = content;
          messages.push({
            id: `m${msgIdx++}`,
            role: "user",
            text: content,
            timestamp: ts,
            kind: "compact-marker",
          });
          continue;
        }
        messages.push({
          id: `m${msgIdx++}`,
          role: "user",
          text: content,
          timestamp: ts,
        });
      } else {
        // process blocks
        let userText = "";
        for (const block of content) {
          if (block.type === "text") {
            const t = (block as RawContentText).text;
            if (!lineIsCompact && isSystemLikeText(t)) {
              // skip
            } else if (lineIsCompact || isCompactContinuationMarker(t)) {
              compacted = true;
              compactSummary = t;
              messages.push({
                id: `m${msgIdx++}`,
                role: "user",
                text: t,
                timestamp: ts,
                kind: "compact-marker",
              });
            } else {
              userText += (userText ? "\n" : "") + t;
            }
          } else if (block.type === "tool_result") {
            const tr = block as RawContentToolResult;
            let resultText = "";
            if (typeof tr.content === "string") {
              resultText = tr.content;
            } else if (Array.isArray(tr.content)) {
              resultText = tr.content
                .map((c) => (typeof c === "object" && c.text ? c.text : ""))
                .join("\n");
            }
            messages.push({
              id: `m${msgIdx++}`,
              role: "tool",
              text: resultText,
              timestamp: ts,
            });
          }
        }
        if (userText) {
          messages.push({
            id: `m${msgIdx++}`,
            role: "user",
            text: userText,
            timestamp: ts,
          });
        }
      }
    } else if (parsed.type === "assistant") {
      const line = parsed as RawAssistantLine;
      const ts = line.timestamp;
      if (ts) {
        if (!startedAt) startedAt = ts;
        endedAt = ts;
      }

      const msg = line.message;
      if (!msg) continue;
      if (msg.model) modelSet.add(msg.model);

      // accumulate tokens
      if (msg.usage) {
        totalInput += msg.usage.input_tokens ?? 0;
        totalOutput += msg.usage.output_tokens ?? 0;
      }

      let assistantText = "";
      let thinkingText = "";

      for (const block of msg.content ?? []) {
        if (block.type === "text") {
          assistantText += (assistantText ? "\n" : "") + (block as RawContentText).text;
        } else if (block.type === "thinking") {
          thinkingText +=
            (thinkingText ? "\n" : "") + (block as RawContentThinking).thinking;
        } else if (block.type === "tool_use") {
          const tu = block as RawContentToolUse;
          const inputKeys = tu.input ? Object.keys(tu.input).join(", ") : "";
          const toolText = inputKeys
            ? `${tu.name}(${inputKeys})`
            : tu.name;
          messages.push({
            id: `m${msgIdx++}`,
            role: "tool",
            toolName: tu.name,
            text: toolText,
            model: msg.model,
            timestamp: ts,
          });
        }
      }

      if (assistantText || thinkingText) {
        messages.push({
          id: `m${msgIdx++}`,
          role: "assistant",
          text: assistantText,
          thinking: thinkingText || undefined,
          model: msg.model,
          timestamp: ts,
        });
      }
    }
    // summary / last-prompt / mode lines → skip
  }

  // derive title/preview from first real user message, skipping compact-marker lines
  const firstUser = messages.find(
    (m) => m.role === "user" && m.kind !== "compact-marker" && m.text.trim().length > 0
  );
  const previewText = firstUser?.text ?? "";
  const preview = previewText.slice(0, 240);
  const title = previewText.slice(0, 80) || sessionId;

  // derive project from cwd
  const project = cwd ? cwd.split("/").filter(Boolean).pop() : undefined;

  const tokens: SessionTokenUsage | undefined =
    totalInput > 0 || totalOutput > 0
      ? { input: totalInput, output: totalOutput }
      : undefined;

  return {
    id: sessionId,
    tool: "claude-code",
    title,
    cwd,
    project,
    startedAt,
    endedAt,
    messageCount: messages.length,
    messages,
    models: [...modelSet],
    tokens,
    preview,
    filePath,
    compacted,
    compactSummary,
  };
}
