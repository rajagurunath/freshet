/** Minimal path helpers that work in both browser and Tauri (no Node dependency). */

export function basename(p: string): string {
  return p.replace(/\\/g, "/").split("/").pop() ?? p;
}

export function extname(p: string): string {
  const b = basename(p);
  const idx = b.lastIndexOf(".");
  if (idx <= 0) return "";
  return b.slice(idx);
}

export function dirname(p: string): string {
  const normalized = p.replace(/\\/g, "/");
  const idx = normalized.lastIndexOf("/");
  if (idx === -1) return ".";
  return normalized.slice(0, idx) || "/";
}

export function joinPath(...parts: string[]): string {
  return parts
    .map((p) => p.replace(/\\/g, "/"))
    .join("/")
    .replace(/\/+/g, "/");
}
