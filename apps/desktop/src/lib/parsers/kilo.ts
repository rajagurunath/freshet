/**
 * Parser for Kilo Code VS Code extension sessions.
 * Each task lives in <globalStorage>/tasks/<taskId>/
 *   - api_conversation_history.json  – Anthropic-style messages array
 *   - ui_messages.json               – timestamps / metadata (optional)
 *   - task_metadata.json             – optional extra metadata
 */
import type { NormalizedSession, SessionMessage } from "../types";

// ─── raw shapes ─────────────────────────────────────────────────────────────

interface KiloContentText {
  type: "text";
  text: string;
}
interface KiloContentToolUse {
  type: "tool_use";
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
}
interface KiloContentToolResult {
  type: "tool_result";
  tool_use_id?: string;
  content?: string | Array<{ type: string; text?: string }>;
}

type KiloBlock = KiloContentText | KiloContentToolUse | KiloContentToolResult;

interface KiloMessage {
  role: "user" | "assistant" | string;
  content: string | KiloBlock[];
}

interface UiMessage {
  type?: string;
  ts?: number;
  timestamp?: string;
  text?: string;
  say?: string;
  ask?: string;
}

interface TaskMetadata {
  id?: string;
  cwd?: string;
  timestamp?: number;
  created_at?: string;
  task?: string;
}

// ─── helpers ────────────────────────────────────────────────────────────────

function flattenContent(content: string | KiloBlock[]): {
  text: string;
  toolMessages: Array<{ name: string; text: string }>;
} {
  if (typeof content === "string") return { text: content, toolMessages: [] };
  let text = "";
  const toolMessages: Array<{ name: string; text: string }> = [];

  for (const block of content) {
    if (block.type === "text") {
      text += (text ? "\n" : "") + (block as KiloContentText).text;
    } else if (block.type === "tool_use") {
      const tu = block as KiloContentToolUse;
      const name = tu.name ?? "unknown";
      const inputKeys = tu.input ? Object.keys(tu.input).join(", ") : "";
      toolMessages.push({ name, text: inputKeys ? `${name}(${inputKeys})` : name });
    } else if (block.type === "tool_result") {
      const tr = block as KiloContentToolResult;
      let resultText = "";
      if (typeof tr.content === "string") {
        resultText = tr.content;
      } else if (Array.isArray(tr.content)) {
        resultText = tr.content.map((c) => c.text ?? "").join("\n");
      }
      toolMessages.push({ name: "tool_result", text: resultText });
    }
  }
  return { text, toolMessages };
}

// ─── main parser ─────────────────────────────────────────────────────────────

export function parseKilo(
  apiHistoryText: string,
  filePath: string,
  taskId: string,
  uiText?: string
): NormalizedSession {
  let apiMessages: KiloMessage[] = [];
  try {
    apiMessages = JSON.parse(apiHistoryText) as KiloMessage[];
  } catch {
    // malformed – return empty session
  }

  // parse ui_messages for timestamps
  let uiMessages: UiMessage[] = [];
  if (uiText) {
    try {
      uiMessages = JSON.parse(uiText) as UiMessage[];
    } catch {
      // optional
    }
  }

  const startedAt: string | undefined = (() => {
    const first = uiMessages.find((u) => u.ts);
    if (first?.ts) return new Date(first.ts).toISOString();
    return undefined;
  })();

  const endedAt: string | undefined = (() => {
    const last = [...uiMessages].reverse().find((u) => u.ts);
    if (last?.ts) return new Date(last.ts).toISOString();
    return undefined;
  })();

  const messages: SessionMessage[] = [];
  let msgIdx = 0;

  for (const raw of apiMessages) {
    const { text, toolMessages } = flattenContent(raw.content);
    const role = raw.role === "user" ? "user" as const : "assistant" as const;

    // emit tool sub-messages first (for user blocks containing tool_result)
    if (role === "user") {
      for (const tm of toolMessages) {
        messages.push({
          id: `m${msgIdx++}`,
          role: "tool",
          toolName: tm.name !== "tool_result" ? tm.name : undefined,
          text: tm.text,
        });
      }
      if (text.trim()) {
        messages.push({ id: `m${msgIdx++}`, role, text });
      }
    } else {
      // assistant: emit tool_use as tool messages
      for (const tm of toolMessages) {
        if (tm.name !== "tool_result") {
          messages.push({
            id: `m${msgIdx++}`,
            role: "tool",
            toolName: tm.name,
            text: tm.text,
          });
        }
      }
      if (text.trim()) {
        messages.push({ id: `m${msgIdx++}`, role, text });
      }
    }
  }

  const firstUser = messages.find((m) => m.role === "user" && m.text.trim().length > 0);
  const previewText = firstUser?.text ?? "";
  const preview = previewText.slice(0, 240);
  const title = previewText.slice(0, 80) || taskId;

  // cwd is not available from api_conversation_history alone; use undefined
  const cwd: string | undefined = undefined;

  return {
    id: taskId,
    tool: "kilo-code",
    title,
    cwd,
    project: undefined,
    startedAt,
    endedAt,
    messageCount: messages.length,
    messages,
    models: [],
    tokens: undefined,
    preview,
    filePath,
  };
}
