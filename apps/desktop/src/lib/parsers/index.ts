/**
 * Parser index: tool detection, file scanning, and session loading.
 */
import type { NormalizedSession, Tool } from "../types";
import { parseClaude } from "./claude";
import { parseCodex } from "./codex";
import { parseKilo } from "./kilo";
import { getSessionRoots, listSessionFiles, readText, statFile, isTauri } from "../tauri";
import { MOCK_SESSIONS } from "../mock";
import { basename, joinPath } from "../path-utils";
import {
  needsParse,
  mergeScanResult,
  type ScanCache,
  type ScanFileInfo,
} from "../scan-cache";

/** Result from scanLocalSessions when cache is used. */
export interface ScanResult {
  sessions: NormalizedSession[];
  updatedCache: ScanCache;
}

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
 *
 * When `previousCache` and `previousSessions` are supplied, only new or
 * changed files are re-parsed (incremental scan).  The returned `updatedCache`
 * should be persisted to the store for the next call.
 */
export async function scanLocalSessions(
  previousCache?: ScanCache,
  previousSessions?: NormalizedSession[],
): Promise<ScanResult> {
  if (!isTauri()) {
    return { sessions: MOCK_SESSIONS, updatedCache: {} };
  }

  const roots = await getSessionRoots();

  // Collect all file paths for each root tool
  const allFiles: { path: string; tool: Tool }[] = [];

  for (const root of roots) {
    const normalized = root.replace(/\\/g, "/");
    try {
      if (normalized.includes("/.claude/projects")) {
        const files = await listSessionFiles(root, ["jsonl"]);
        for (const f of files) allFiles.push({ path: f, tool: "claude-code" });
      } else if (normalized.includes("/.codex/sessions")) {
        const files = await listSessionFiles(root, ["jsonl"]);
        for (const f of files) allFiles.push({ path: f, tool: "codex" });
      } else if (normalized.includes("kilocode.kilo-code")) {
        // Kilo: gather the api_conversation_history.json paths
        const tasksDir = joinPath(root, "tasks");
        const jsonFiles = await listSessionFiles(tasksDir, ["json"]);
        for (const f of jsonFiles) {
          if (basename(f) === "api_conversation_history.json") {
            allFiles.push({ path: f, tool: "kilo-code" });
          }
        }
      }
    } catch (err) {
      console.warn("[context-hub] Failed to scan root:", root, err);
    }
  }

  // Fetch mtime/size for all discovered files in parallel
  const fileInfoMap: Record<string, ScanFileInfo> = {};
  await Promise.all(
    allFiles.map(async ({ path }) => {
      try {
        const info = await statFile(path);
        fileInfoMap[path] = info;
      } catch {
        // File disappeared between listing and stat — skip it
      }
    }),
  );

  const cache = previousCache ?? {};
  const prev = previousSessions ?? [];
  const prevPaths = new Set(prev.map((s) => s.filePath).filter(Boolean));

  // Determine which files need (re-)parsing. A cache-fresh file still needs
  // parsing when its session is not in memory (fresh app launch).
  const toParseFiles = allFiles.filter(({ path }) => {
    const info = fileInfoMap[path];
    if (!info) return false; // stat failed; skip
    return needsParse(cache[path], info, prevPaths.has(path));
  });

  // Parse only the stale/new files
  const freshSessions: NormalizedSession[] = [];
  await parseFiles(toParseFiles, freshSessions);

  // Merge with cached sessions
  const { sessions, updatedCache } = mergeScanResult(prev, freshSessions, cache, fileInfoMap);

  sessions.sort((a, b) => {
    const ta = a.startedAt ?? "";
    const tb = b.startedAt ?? "";
    return tb.localeCompare(ta);
  });

  return { sessions, updatedCache };
}

/** Parse a list of {path, tool} entries into `out`, handling kilo grouping. */
async function parseFiles(
  files: { path: string; tool: Tool }[],
  out: NormalizedSession[],
): Promise<void> {
  // Group kilo files by taskId so we can pair api + ui history
  const kiloGroup = new Map<string, { apiFile?: string; uiFile?: string }>();

  for (const { path, tool } of files) {
    if (tool === "claude-code") {
      try {
        const text = await readText(path);
        out.push(parseClaude(text, path));
      } catch (err) {
        console.warn("[context-hub] Failed to parse Claude file:", path, err);
      }
    } else if (tool === "codex") {
      try {
        const text = await readText(path);
        out.push(parseCodex(text, path));
      } catch (err) {
        console.warn("[context-hub] Failed to parse Codex file:", path, err);
      }
    } else if (tool === "kilo-code") {
      const parts = path.replace(/\\/g, "/").split("/");
      const tasksIdx = parts.lastIndexOf("tasks");
      if (tasksIdx === -1 || tasksIdx + 1 >= parts.length) continue;
      const taskId = parts[tasksIdx + 1];
      if (!kiloGroup.has(taskId)) kiloGroup.set(taskId, {});
      const entry = kiloGroup.get(taskId)!;
      const fn = basename(path);
      if (fn === "api_conversation_history.json") entry.apiFile = path;
      else if (fn === "ui_messages.json") entry.uiFile = path;
    }
  }

  // Parse kilo groups
  for (const [taskId, entry] of kiloGroup) {
    if (!entry.apiFile) continue;
    try {
      const apiText = await readText(entry.apiFile);
      const uiText = entry.uiFile ? await readText(entry.uiFile) : undefined;
      out.push(parseKilo(apiText, entry.apiFile, taskId, uiText));
    } catch (err) {
      console.warn("[context-hub] Failed to parse Kilo task:", taskId, err);
    }
  }
}

