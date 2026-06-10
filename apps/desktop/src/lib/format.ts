/**
 * Small display-layer helpers. Pure functions, no side effects.
 */
import type { Category, Tool } from "./types";
import { formatDistanceToNow, parseISO } from "date-fns";

// ─── time ─────────────────────────────────────────────────────────────────────

/**
 * Returns a human-readable relative time string, e.g. "3 hours ago".
 * Gracefully handles undefined / unparseable input.
 */
export function relativeTime(iso: string | undefined): string {
  if (!iso) return "unknown";
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true });
  } catch {
    return "unknown";
  }
}

// ─── tokens ──────────────────────────────────────────────────────────────────

/**
 * Formats a raw token count into a compact readable string.
 * e.g. 1200 → "1.2k", 1_500_000 → "1.5M"
 */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

// ─── text ─────────────────────────────────────────────────────────────────────

/**
 * Truncates a string to at most `n` characters, appending "…" if cut.
 */
export function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

// ─── tool labels ─────────────────────────────────────────────────────────────

export function toolLabel(tool: Tool): string {
  switch (tool) {
    case "claude-code": return "Claude Code";
    case "codex":       return "Codex";
    case "kilo-code":   return "Kilo Code";
    default: {
      const _exhaustive: never = tool;
      return _exhaustive;
    }
  }
}

// ─── category colors (Tailwind class pairs) ───────────────────────────────────

/**
 * Returns a Tailwind background + text class pair for the given category,
 * suitable for badge/chip usage.
 */
export function categoryColor(cat: Category): string {
  switch (cat) {
    case "engineering": return "bg-blue-100 text-blue-800";
    case "sales":       return "bg-green-100 text-green-800";
    case "marketing":   return "bg-purple-100 text-purple-800";
    case "research":    return "bg-yellow-100 text-yellow-800";
    case "ops":         return "bg-orange-100 text-orange-800";
    case "other":       return "bg-gray-100 text-gray-700";
    default: {
      const _exhaustive: never = cat;
      return _exhaustive;
    }
  }
}
