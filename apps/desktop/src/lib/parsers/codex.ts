/**
 * Parser for Codex JSONL session files.
 * Path pattern: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl
 * First line type:"session_meta", rest are type:"response_item".
 */
import type { NormalizedSession, SessionMessage } from "../types";

// ─── raw line shapes ────────────────────────────────────────────────────────

interface SessionMetaPayload {
  id?: string;
  timestamp?: string;
  cwd?: string;
  cli_version?: string;
  originator?: string;
  instructions?: string;
}

interface SessionMetaLine {
  type: "session_meta";
  payload: SessionMetaPayload;
}

interface ContentBlock {
  type: "input_text" | "output_text" | string;
  text?: string;
}

interface MessagePayload {
  type: "message";
  role: "user" | "assistant" | string;
  content?: ContentBlock[];
}

interface FunctionCallPayload {
  type: "function_call";
  name?: string;
  arguments?: string;
  call_id?: string;
}

interface FunctionCallOutputPayload {
  type: "function_call_output";
  output?: string;
  call_id?: string;
}

interface ReasoningPayload {
  type: "reasoning";
  summary?: Array<{ type: string; text?: string }> | string;
  content?: Array<{ type: string; text?: string }> | string;
}

type ResponsePayload =
  | MessagePayload
  | FunctionCallPayload
  | FunctionCallOutputPayload
  | ReasoningPayload
  | { type: string; [k: string]: unknown };

interface ResponseItemLine {
  type: "response_item";
  payload: ResponsePayload;
}

type RawLine =
  | SessionMetaLine
  | ResponseItemLine
  | { type: string; [k: string]: unknown };

// ─── helpers ────────────────────────────────────────────────────────────────

function extractReasoningText(p: ReasoningPayload): string {
  const raw = p.summary ?? p.content;
  if (!raw) return "";
  if (typeof raw === "string") return raw;
  return raw.map((b) => b.text ?? "").join("\n");
}

function extractContentText(blocks: ContentBlock[] | undefined): string {
  if (!blocks) return "";
  return blocks.map((b) => b.text ?? "").join("\n");
}

// ─── main parser ─────────────────────────────────────────────────────────────

export function parseCodex(text: string, filePath: string): NormalizedSession {
  const lines = text.split("\n").filter((l) => l.trim().length > 0);

  let sessionId = filePath.replace(/\\/g, "/").split("/").pop()?.replace(".jsonl", "") ?? filePath;
  let cwd: string | undefined;
  let startedAt: string | undefined;
  const messages: SessionMessage[] = [];
  let msgIdx = 0;

  // pending reasoning to attach to next assistant message
  let pendingThinking = "";

  for (const rawLine of lines) {
    let parsed: RawLine;
    try {
      parsed = JSON.parse(rawLine) as RawLine;
    } catch {
      continue;
    }

    if (parsed.type === "session_meta") {
      const meta = (parsed as SessionMetaLine).payload;
      if (meta.id) sessionId = meta.id;
      if (meta.cwd) cwd = meta.cwd;
      if (meta.timestamp) startedAt = meta.timestamp;
      continue;
    }

    if (parsed.type !== "response_item") continue;
    const payload = (parsed as ResponseItemLine).payload;
    if (!payload) continue;

    if (payload.type === "reasoning") {
      const reasoningText = extractReasoningText(payload as ReasoningPayload);
      if (reasoningText) {
        pendingThinking += (pendingThinking ? "\n" : "") + reasoningText;
      }
      continue;
    }

    if (payload.type === "message") {
      const mp = payload as MessagePayload;
      const contentText = extractContentText(mp.content);
      if (!contentText.trim()) continue;

      const role = mp.role === "user" ? "user" as const : "assistant" as const;

      if (role === "assistant" && pendingThinking) {
        messages.push({
          id: `m${msgIdx++}`,
          role,
          text: contentText,
          thinking: pendingThinking,
        });
        pendingThinking = "";
      } else {
        messages.push({
          id: `m${msgIdx++}`,
          role,
          text: contentText,
        });
      }
      continue;
    }

    if (payload.type === "function_call") {
      const fc = payload as FunctionCallPayload;
      const name = fc.name ?? "unknown";
      let argStr = "";
      if (fc.arguments) {
        try {
          const parsed2 = JSON.parse(fc.arguments) as Record<string, unknown>;
          argStr = Object.keys(parsed2).join(", ");
        } catch {
          argStr = fc.arguments.slice(0, 60);
        }
      }
      const toolText = argStr ? `${name}(${argStr})` : name;
      messages.push({
        id: `m${msgIdx++}`,
        role: "tool",
        toolName: name,
        text: toolText,
      });
      continue;
    }

    if (payload.type === "function_call_output") {
      const fco = payload as FunctionCallOutputPayload;
      messages.push({
        id: `m${msgIdx++}`,
        role: "tool",
        text: fco.output ?? "",
      });
      continue;
    }
  }

  // flush any trailing reasoning as its own assistant message
  if (pendingThinking) {
    messages.push({
      id: `m${msgIdx++}`,
      role: "assistant",
      text: "",
      thinking: pendingThinking,
    });
    pendingThinking = "";
  }

  const firstUser = messages.find((m) => m.role === "user" && m.text.trim().length > 0);
  const previewText = firstUser?.text ?? "";
  const preview = previewText.slice(0, 240);
  const title = previewText.slice(0, 80) || sessionId;
  const project = cwd ? cwd.split("/").filter(Boolean).pop() : undefined;

  return {
    id: sessionId,
    tool: "codex",
    title,
    cwd,
    project,
    startedAt,
    endedAt: undefined,
    messageCount: messages.length,
    messages,
    models: [],
    tokens: undefined,
    preview,
    filePath,
  };
}
