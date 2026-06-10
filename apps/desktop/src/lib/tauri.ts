/**
 * Thin bridge over Tauri IPC. All functions gracefully no-op when running in a
 * browser (dev/demo mode) so the rest of the codebase doesn't need to guard.
 */

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
