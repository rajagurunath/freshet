/**
 * Thin bridge over Tauri IPC. All functions gracefully no-op when running in a
 * browser (dev/demo mode) so the rest of the codebase doesn't need to guard.
 */
import type { ScanFileInfo } from "./scan-cache";
import type { DirEntry } from "./assetScan";

export function isTauri(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export async function getSessionRoots(): Promise<string[]> {
  if (!isTauri()) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string[]>("session_roots");
}

export async function listSessionFiles(
  dir: string,
  exts: string[]
): Promise<string[]> {
  if (!isTauri()) return [];
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string[]>("list_session_files", { dir, exts });
}

export async function readText(path: string): Promise<string> {
  if (!isTauri()) return "";
  const { readTextFile } = await import("@tauri-apps/plugin-fs");
  return readTextFile(path);
}

/**
 * Write UTF-8 text to `path`. Used by branch-from-turn to create a new session
 * file. The Tauri capability scopes writes to `$HOME/.claude/projects/**` so a
 * branch can never modify a file outside the Claude projects tree. No-ops in
 * browser/dev mode.
 */
export async function writeText(path: string, content: string): Promise<void> {
  if (!isTauri()) return;
  const { writeTextFile } = await import("@tauri-apps/plugin-fs");
  await writeTextFile(path, content);
}

/**
 * Return mtime (Unix ms) and byte-size for the given file.
 * Throws if the file does not exist or stat fails.
 */
export async function statFile(path: string): Promise<ScanFileInfo> {
  if (!isTauri()) return { mtime: 0, size: 0 };
  const { stat } = await import("@tauri-apps/plugin-fs");
  const meta = await stat(path);
  return {
    mtime: meta.mtime ? new Date(meta.mtime).getTime() : 0,
    size: meta.size ?? 0,
  };
}

/** Absolute home directory path, or "" in browser/dev mode. */
export async function homeDirPath(): Promise<string> {
  if (!isTauri()) return "";
  const { homeDir } = await import("@tauri-apps/api/path");
  return homeDir();
}

/**
 * List a directory's entries (name + isDirectory). Returns [] when the
 * directory does not exist, is unreadable, or we are in browser/dev mode —
 * the asset scanner treats a missing root as "no assets", not an error.
 */
export async function listDir(path: string): Promise<DirEntry[]> {
  if (!isTauri()) return [];
  try {
    const { readDir } = await import("@tauri-apps/plugin-fs");
    const entries = await readDir(path);
    return entries.map((e) => ({ name: e.name, isDirectory: e.isDirectory }));
  } catch {
    return [];
  }
}

/** Read a file's raw bytes (used when zipping assets for upload). */
export async function readBinary(path: string): Promise<Uint8Array> {
  if (!isTauri()) return new Uint8Array();
  const { readFile } = await import("@tauri-apps/plugin-fs");
  return readFile(path);
}

/**
 * Start the Rust `notify` file watcher for the given root directories.
 * The watcher debounces events by 2 s and emits `session-file-changed` Tauri
 * events with `{ path: string }` payloads whenever a session file changes.
 *
 * No-ops in browser/dev mode.
 */
export async function startWatching(roots: string[]): Promise<void> {
  if (!isTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke<void>("start_watching", { roots });
}
