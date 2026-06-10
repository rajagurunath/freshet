/**
 * Parser index: tool detection, file scanning, and session loading.
 */
import type { NormalizedSession, Tool } from "../types";
import { parseClaude } from "./claude";
import { parseCodex } from "./codex";
import { parseKilo } from "./kilo";
import { getSessionRoots, listSessionFiles, readText, isTauri } from "../tauri";
import { MOCK_SESSIONS } from "../mock";
import { basename, dirname, joinPath } from "../path-utils";

// ─── tool detection ──────────────────────────────────────────────────────────

export function detectTool(path: string): Tool | null {
  const normalized = path.replace(/\\/g, "/");
  if (normalized.includes("/.claude/projects/") || normalized.includes("/.claude\\projects\\")) {
    return "claude-code";
  }
  if (normalized.includes("/.codex/sessions/") || normalized.includes("/.codex\\sessions\\")) {
    return "codex";
  }
  if (
    normalized.includes("kilocode.kilo-code") ||
    normalized.includes("kilo-code") ||
    (normalized.includes("globalStorage") && normalized.includes("api_conversation_history"))
  ) {
    return "kilo-code";
  }
  return null;
}

// ─── parse a single file ─────────────────────────────────────────────────────

export function parseSessionFile(
  path: string,
  text: string,
  tool: Tool
): NormalizedSession {
  switch (tool) {
    case "claude-code":
      return parseClaude(text, path);
    case "codex":
      return parseCodex(text, path);
    case "kilo-code":
      // kilo passes the taskId as the parent dir name
      return parseKilo(text, path, extractKiloTaskId(path));
    default: {
      // exhaustive check
      const _exhaustive: never = tool;
      throw new Error(`Unknown tool: ${_exhaustive}`);
    }
  }
}

function extractKiloTaskId(filePath: string): string {
  const parts = filePath.replace(/\\/g, "/").split("/");
  const tasksIdx = parts.lastIndexOf("tasks");
  if (tasksIdx !== -1 && tasksIdx + 1 < parts.length) {
    return parts[tasksIdx + 1];
  }
  return basename(filePath).replace(/\.json$/, "");
}

// ─── scan all local sessions ─────────────────────────────────────────────────

/**
 * Discovers and parses all local AI-assistant sessions.
 * Falls back to MOCK_SESSIONS when Tauri is not available.
 */
export async function scanLocalSessions(): Promise<NormalizedSession[]> {
  if (!isTauri()) return MOCK_SESSIONS;

  const roots = await getSessionRoots();
  const sessions: NormalizedSession[] = [];

  for (const root of roots) {
    const normalized = root.replace(/\\/g, "/");

    try {
      if (normalized.includes("/.claude/projects")) {
        // Claude Code: *.jsonl files
        const files = await listSessionFiles(root, ["jsonl"]);
        for (const file of files) {
          try {
            const text = await readText(file);
            const session = parseClaude(text, file);
            sessions.push(session);
          } catch (err) {
            console.warn("[context-hub] Failed to parse Claude file:", file, err);
          }
        }
      } else if (normalized.includes("/.codex/sessions")) {
        // Codex: *.jsonl files
        const files = await listSessionFiles(root, ["jsonl"]);
        for (const file of files) {
          try {
            const text = await readText(file);
            const session = parseCodex(text, file);
            sessions.push(session);
          } catch (err) {
            console.warn("[context-hub] Failed to parse Codex file:", file, err);
          }
        }
      } else if (normalized.includes("kilocode.kilo-code")) {
        // Kilo Code: tasks/<taskId>/api_conversation_history.json
        await scanKiloRoot(root, sessions);
      }
    } catch (err) {
      console.warn("[context-hub] Failed to scan root:", root, err);
    }
  }

  sessions.sort((a, b) => {
    const ta = a.startedAt ?? "";
    const tb = b.startedAt ?? "";
    return tb.localeCompare(ta);
  });

  return sessions;
}

async function scanKiloRoot(
  root: string,
  sessions: NormalizedSession[]
): Promise<void> {
  const tasksDir = joinPath(root, "tasks");
  // list all json files under tasks/
  const files = await listSessionFiles(tasksDir, ["json"]);

  // group by taskId (parent dir)
  const taskMap = new Map<string, { apiFile?: string; uiFile?: string }>();
  for (const file of files) {
    const parts = file.replace(/\\/g, "/").split("/");
    const tasksIdx = parts.lastIndexOf("tasks");
    if (tasksIdx === -1 || tasksIdx + 1 >= parts.length) continue;
    const taskId = parts[tasksIdx + 1];
    const fileName = basename(file);

    if (!taskMap.has(taskId)) taskMap.set(taskId, {});
    const entry = taskMap.get(taskId)!;
    if (fileName === "api_conversation_history.json") entry.apiFile = file;
    else if (fileName === "ui_messages.json") entry.uiFile = file;
  }

  for (const [taskId, entry] of taskMap) {
    if (!entry.apiFile) continue;
    try {
      const apiText = await readText(entry.apiFile);
      const uiText = entry.uiFile ? await readText(entry.uiFile) : undefined;
      const session = parseKilo(apiText, entry.apiFile, taskId, uiText);
      sessions.push(session);
    } catch (err) {
      console.warn("[context-hub] Failed to parse Kilo task:", taskId, err);
    }
  }
}
