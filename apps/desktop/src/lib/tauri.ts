/**
 * Thin bridge over Tauri IPC. All functions gracefully no-op when running in a
 * browser (dev/demo mode) so the rest of the codebase doesn't need to guard.
 */
import type { ScanFileInfo } from "./scan-cache";

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
