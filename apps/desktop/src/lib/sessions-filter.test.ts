/**
 * Tests for sorting and filtering logic used on the sessions list page.
 * These functions are extracted from SessionsPage so they can be tested
 * in isolation without a DOM environment.
 */
import { describe, expect, it } from "vitest";
import { filterSessions, sortSessions } from "./sessions-filter";
import type { NormalizedSession } from "./types";
import type { SessionListPrefs } from "../store/app";

// ─── helpers ─────────────────────────────────────────────────────────────────

function makeSession(
  overrides: Partial<NormalizedSession> & { id: string }
): NormalizedSession {
  return {
    tool: "claude-code",
    title: "Test session",
    messageCount: 5,
    messages: [],
    models: [],
    preview: "test preview",
    filePath: "/tmp/sess.jsonl",
    ...overrides,
  };
}

const BASE_PREFS: SessionListPrefs = {
  sortField: "date",
  sortOrder: "desc",
  dateRange: "all",
  compactedOnly: false,
};

// ─── filterSessions ──────────────────────────────────────────────────────────

describe("filterSessions", () => {
  const sessions: NormalizedSession[] = [
    makeSession({ id: "a", tool: "claude-code", project: "alpha", title: "Alpha session", startedAt: "2024-01-15T00:00:00Z" }),
    makeSession({ id: "b", tool: "codex", project: "beta", title: "Beta session", startedAt: "2024-01-10T00:00:00Z" }),
    makeSession({ id: "c", tool: "kilo-code", project: "alpha", title: "Gamma session", startedAt: "2024-01-05T00:00:00Z" }),
    makeSession({ id: "d", tool: "claude-code", project: "gamma", title: "Delta session", compacted: true, startedAt: "2024-01-20T00:00:00Z" }),
  ];

  it("returns all sessions when no filters are active", () => {
    const result = filterSessions(sessions, { tab: "all", search: "", project: "all", prefs: BASE_PREFS });
    expect(result).toHaveLength(4);
  });

  it("filters by tool tab", () => {
    const result = filterSessions(sessions, { tab: "claude-code", search: "", project: "all", prefs: BASE_PREFS });
    expect(result).toHaveLength(2);
    expect(result.every((s) => s.tool === "claude-code")).toBe(true);
  });

  it("filters by text search (title match)", () => {
    // "Alpha session" title is unique to session "a"; session "c" has project "alpha"
    // so we use a title that only matches one session
    const result = filterSessions(sessions, { tab: "all", search: "Alpha session", project: "all", prefs: BASE_PREFS });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("a");
  });

  it("filters by text search (project match)", () => {
    const result = filterSessions(sessions, { tab: "all", search: "beta", project: "all", prefs: BASE_PREFS });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("b");
  });

  it("filters by text search (preview match)", () => {
    const s = [makeSession({ id: "x", title: "nothing", preview: "unique-preview-text" })];
    const result = filterSessions(s, { tab: "all", search: "unique-preview-text", project: "all", prefs: BASE_PREFS });
    expect(result).toHaveLength(1);
  });

  it("filters by project dropdown", () => {
    const result = filterSessions(sessions, { tab: "all", search: "", project: "alpha", prefs: BASE_PREFS });
    expect(result).toHaveLength(2);
    expect(result.every((s) => s.project === "alpha")).toBe(true);
  });

  it("filters compacted only when flag is set", () => {
    const prefs = { ...BASE_PREFS, compactedOnly: true };
    const result = filterSessions(sessions, { tab: "all", search: "", project: "all", prefs });
    expect(result).toHaveLength(1);
    expect(result[0]!.compacted).toBe(true);
  });

  it("combines tool tab and text search", () => {
    const result = filterSessions(sessions, { tab: "claude-code", search: "delta", project: "all", prefs: BASE_PREFS });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("d");
  });

  it("filters by date range (last 7 days)", () => {
    // Use future-relative startedAt to ensure "recent" sessions pass the cutoff
    const now = Date.now();
    const recent = new Date(now - 2 * 24 * 60 * 60 * 1000).toISOString(); // 2 days ago
    const old = new Date(now - 30 * 24 * 60 * 60 * 1000).toISOString(); // 30 days ago
    const s = [
      makeSession({ id: "new", startedAt: recent }),
      makeSession({ id: "old", startedAt: old }),
    ];
    const prefs = { ...BASE_PREFS, dateRange: "7d" as const };
    const result = filterSessions(s, { tab: "all", search: "", project: "all", prefs });
    expect(result).toHaveLength(1);
    expect(result[0]!.id).toBe("new");
  });

  it("returns all sessions when dateRange is 'all'", () => {
    const s = [
      makeSession({ id: "a", startedAt: "2000-01-01T00:00:00Z" }),
      makeSession({ id: "b", startedAt: "2024-12-31T00:00:00Z" }),
    ];
    const result = filterSessions(s, { tab: "all", search: "", project: "all", prefs: BASE_PREFS });
    expect(result).toHaveLength(2);
  });
});

// ─── sortSessions ─────────────────────────────────────────────────────────────

describe("sortSessions", () => {
  const sessions: NormalizedSession[] = [
    makeSession({
      id: "a",
      title: "Alice session",
      startedAt: "2024-01-01T00:00:00Z",
      messageCount: 10,
      tokens: { input: 1000, output: 500 },
      models: ["claude-sonnet-4-5"],
      project: "zebra",
      tool: "claude-code",
    }),
    makeSession({
      id: "b",
      title: "Bob session",
      startedAt: "2024-03-01T00:00:00Z",
      messageCount: 5,
      tokens: { input: 500, output: 100 },
      models: ["claude-haiku-4-5"],
      project: "apple",
      tool: "codex",
    }),
    makeSession({
      id: "c",
      title: "Carol session",
      startedAt: "2024-02-01T00:00:00Z",
      messageCount: 20,
      tokens: { input: 2000, output: 1000 },
      models: ["claude-opus-4-5"],
      project: "mango",
      tool: "kilo-code",
    }),
  ];

  it("sorts by date desc (newest first)", () => {
    const prefs = { ...BASE_PREFS, sortField: "date" as const, sortOrder: "desc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result.map((s) => s.id)).toEqual(["b", "c", "a"]);
  });

  it("sorts by date asc (oldest first)", () => {
    const prefs = { ...BASE_PREFS, sortField: "date" as const, sortOrder: "asc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result.map((s) => s.id)).toEqual(["a", "c", "b"]);
  });

  it("sorts by messages desc", () => {
    const prefs = { ...BASE_PREFS, sortField: "messages" as const, sortOrder: "desc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result[0]!.id).toBe("c"); // 20 messages
    expect(result[2]!.id).toBe("b"); // 5 messages
  });

  it("sorts by messages asc", () => {
    const prefs = { ...BASE_PREFS, sortField: "messages" as const, sortOrder: "asc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result[0]!.id).toBe("b"); // 5 messages
    expect(result[2]!.id).toBe("c"); // 20 messages
  });

  it("sorts by tokens desc", () => {
    const prefs = { ...BASE_PREFS, sortField: "tokens" as const, sortOrder: "desc" as const };
    const result = sortSessions(sessions, prefs);
    // c: 3000 total, a: 1500 total, b: 600 total
    expect(result.map((s) => s.id)).toEqual(["c", "a", "b"]);
  });

  it("sorts by tokens asc", () => {
    const prefs = { ...BASE_PREFS, sortField: "tokens" as const, sortOrder: "asc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result.map((s) => s.id)).toEqual(["b", "a", "c"]);
  });

  it("sorts by project alphabetically asc", () => {
    const prefs = { ...BASE_PREFS, sortField: "project" as const, sortOrder: "asc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result[0]!.project).toBe("apple");
    expect(result[2]!.project).toBe("zebra");
  });

  it("sorts by project alphabetically desc", () => {
    const prefs = { ...BASE_PREFS, sortField: "project" as const, sortOrder: "desc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result[0]!.project).toBe("zebra");
    expect(result[2]!.project).toBe("apple");
  });

  it("sorts by tool alphabetically asc", () => {
    const prefs = { ...BASE_PREFS, sortField: "tool" as const, sortOrder: "asc" as const };
    const result = sortSessions(sessions, prefs);
    expect(result[0]!.tool).toBe("claude-code");
    expect(result[2]!.tool).toBe("kilo-code");
  });

  it("sorts by cost desc", () => {
    const prefs = { ...BASE_PREFS, sortField: "cost" as const, sortOrder: "desc" as const };
    const result = sortSessions(sessions, prefs);
    // opus-4-5 is most expensive per token, so c should be first
    expect(result[0]!.id).toBe("c");
    // haiku is cheapest; b has fewer tokens and haiku rate
    expect(result[2]!.id).toBe("b");
  });

  it("does not mutate the input array", () => {
    const prefs = { ...BASE_PREFS, sortField: "date" as const, sortOrder: "asc" as const };
    const original = [...sessions];
    sortSessions(sessions, prefs);
    expect(sessions.map((s) => s.id)).toEqual(original.map((s) => s.id));
  });
});

// ─── deriveProjects ──────────────────────────────────────────────────────────

describe("deriveProjects", () => {
  it("extracts unique non-empty project values", async () => {
    const { deriveProjects } = await import("./sessions-filter");
    const sessions: NormalizedSession[] = [
      makeSession({ id: "1", project: "alpha" }),
      makeSession({ id: "2", project: "beta" }),
      makeSession({ id: "3", project: "alpha" }),
      makeSession({ id: "4" }), // no project
    ];
    const projects = deriveProjects(sessions);
    expect(projects).toContain("alpha");
    expect(projects).toContain("beta");
    expect(projects).not.toContain(undefined);
    expect(new Set(projects).size).toBe(projects.length); // unique
  });
});
