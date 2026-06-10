/**
 * Pure filtering and sorting helpers for the sessions list.
 * Extracted so they can be unit-tested without a DOM environment.
 */
import type { NormalizedSession, Tool } from "./types";
import type { SessionListPrefs, SortField } from "../store/app";
import { estimateCost } from "./pricing";

// ─── public filter params ─────────────────────────────────────────────────────

export interface SessionFilterParams {
  /** Active tool tab ("all" or a specific tool). */
  tab: "all" | Tool;
  /** Free-text search string (case-insensitive). */
  search: string;
  /** Project filter ("all" or an exact project name). */
  project: string;
  /** Date-range + compactedOnly from the persisted prefs. */
  prefs: Pick<SessionListPrefs, "dateRange" | "compactedOnly">;
}

// ─── date range helper ────────────────────────────────────────────────────────

function cutoffMs(range: SessionListPrefs["dateRange"]): number {
  if (range === "all") return 0;
  const days = range === "7d" ? 7 : range === "30d" ? 30 : 90;
  return Date.now() - days * 24 * 60 * 60 * 1000;
}

// ─── filterSessions ──────────────────────────────────────────────────────────

/**
 * Apply all client-side filters to a session list.
 * Returns a new array; the original is not mutated.
 */
export function filterSessions(
  sessions: NormalizedSession[],
  { tab, search, project, prefs }: SessionFilterParams,
): NormalizedSession[] {
  const q = search.trim().toLowerCase();
  const cutoff = cutoffMs(prefs.dateRange);

  return sessions.filter((s) => {
    // Tool tab
    if (tab !== "all" && s.tool !== tab) return false;

    // Project dropdown
    if (project !== "all" && s.project !== project) return false;

    // Compacted only
    if (prefs.compactedOnly && !s.compacted) return false;

    // Date range
    if (cutoff > 0) {
      const ts = s.startedAt ? new Date(s.startedAt).getTime() : 0;
      if (ts < cutoff) return false;
    }

    // Text search
    if (q) {
      const inTitle = s.title.toLowerCase().includes(q);
      const inPreview = (s.preview ?? "").toLowerCase().includes(q);
      const inProject = (s.project ?? "").toLowerCase().includes(q);
      if (!inTitle && !inPreview && !inProject) return false;
    }

    return true;
  });
}

// ─── sortSessions ─────────────────────────────────────────────────────────────

/** Extract a numeric sort key for the given field. */
function numericKey(session: NormalizedSession, field: SortField): number {
  switch (field) {
    case "date":
      return session.startedAt ? new Date(session.startedAt).getTime() : 0;
    case "messages":
      return session.messageCount ?? 0;
    case "tokens":
      return (session.tokens?.input ?? 0) + (session.tokens?.output ?? 0);
    case "cost":
      return estimateCost(session).usd;
    default:
      return 0;
  }
}

/** Extract a string sort key for the given field. */
function stringKey(session: NormalizedSession, field: SortField): string {
  switch (field) {
    case "project":
      return (session.project ?? "").toLowerCase();
    case "tool":
      return session.tool.toLowerCase();
    default:
      return "";
  }
}

const STRING_SORT_FIELDS: SortField[] = ["project", "tool"];

/**
 * Sort a session list according to the persisted preferences.
 * Returns a new array; the original is not mutated.
 */
export function sortSessions(
  sessions: NormalizedSession[],
  prefs: Pick<SessionListPrefs, "sortField" | "sortOrder">,
): NormalizedSession[] {
  const { sortField, sortOrder } = prefs;
  const dir = sortOrder === "asc" ? 1 : -1;

  return [...sessions].sort((a, b) => {
    if (STRING_SORT_FIELDS.includes(sortField)) {
      const ak = stringKey(a, sortField);
      const bk = stringKey(b, sortField);
      return dir * ak.localeCompare(bk);
    }
    const ak = numericKey(a, sortField);
    const bk = numericKey(b, sortField);
    return dir * (ak - bk);
  });
}

// ─── deriveProjects ──────────────────────────────────────────────────────────

/**
 * Derive the sorted list of unique project values from a session list,
 * excluding undefined/empty entries.
 */
export function deriveProjects(sessions: NormalizedSession[]): string[] {
  const set = new Set<string>();
  for (const s of sessions) {
    if (s.project) set.add(s.project);
  }
  return [...set].sort();
}
