/**
 * Tests for the auto-sync engine (Task 10).
 *
 * The module is tested entirely through its pure logic functions — no Tauri
 * runtime, no real timers (vi.useFakeTimers), no real API calls.  All I/O is
 * injected via the `AutoSyncDeps` interface.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  shouldSync,
  computeSessionHash,
  isQuietPeriodElapsed,
  buildPendingQueue,
  processSyncQueue,
  reconcilePushedIds,
  type AutoSyncDeps,
  type SyncState,
  type PendingFile,
} from "./autosync";
import type { NormalizedSession } from "./types";

// ─── helpers ──────────────────────────────────────────────────────────────────

function makeSession(overrides: Partial<NormalizedSession> = {}): NormalizedSession {
  return {
    id: "sess-1",
    title: "Test session",
    tool: "claude-code",
    messages: [],
    messageCount: 3,
    models: ["claude-opus-4-8"],
    preview: "some preview",
    filePath: "/Users/alice/.claude/projects/p1/sess-1.jsonl",
    startedAt: "2026-06-10T10:00:00Z",
    ...overrides,
  };
}

const BASE_SETTINGS = {
  syncMode: "auto" as const,
  autoSyncTools: ["claude-code" as const, "codex" as const],
  redactBeforePush: false,
  defaultCategory: "engineering" as const,
  defaultVisibility: "company" as const,
  apiBaseUrl: "http://localhost:8787",
  apiKey: "test-key",
  author: { id: "user-1", email: "user@example.com", name: "Alice" },
};

// ─── shouldSync ───────────────────────────────────────────────────────────────

describe("shouldSync", () => {
  it("returns true when syncMode is auto and tool is in autoSyncTools", () => {
    expect(
      shouldSync(BASE_SETTINGS, "claude-code"),
    ).toBe(true);
  });

  it("returns false when syncMode is manual", () => {
    expect(
      shouldSync({ ...BASE_SETTINGS, syncMode: "manual" }, "claude-code"),
    ).toBe(false);
  });

  it("returns false when tool is not in autoSyncTools", () => {
    expect(
      shouldSync({ ...BASE_SETTINGS, autoSyncTools: ["codex"] }, "claude-code"),
    ).toBe(false);
  });

  it("returns false when autoSyncTools is empty", () => {
    expect(
      shouldSync({ ...BASE_SETTINGS, autoSyncTools: [] }, "claude-code"),
    ).toBe(false);
  });
});

// ─── computeSessionHash ───────────────────────────────────────────────────────

describe("computeSessionHash", () => {
  it("returns a deterministic non-empty string for the same session", () => {
    const s = makeSession();
    const h1 = computeSessionHash(s);
    const h2 = computeSessionHash(s);
    expect(h1).toBe(h2);
    expect(h1.length).toBeGreaterThan(0);
  });

  it("returns different hashes for sessions with different ids", () => {
    const s1 = makeSession({ id: "a" });
    const s2 = makeSession({ id: "b" });
    expect(computeSessionHash(s1)).not.toBe(computeSessionHash(s2));
  });

  it("returns different hashes when messageCount changes", () => {
    const s1 = makeSession({ messageCount: 3 });
    const s2 = makeSession({ messageCount: 7 });
    expect(computeSessionHash(s1)).not.toBe(computeSessionHash(s2));
  });
});

// ─── isQuietPeriodElapsed ─────────────────────────────────────────────────────

describe("isQuietPeriodElapsed", () => {
  it("returns false when the file was modified less than quietMs ago", () => {
    const now = 1_000_000;
    const lastModified = now - 4 * 60 * 1000; // 4 min ago
    expect(isQuietPeriodElapsed(lastModified, now, 5 * 60 * 1000)).toBe(false);
  });

  it("returns true when the file was modified exactly quietMs ago", () => {
    const now = 1_000_000;
    const lastModified = now - 5 * 60 * 1000;
    expect(isQuietPeriodElapsed(lastModified, now, 5 * 60 * 1000)).toBe(true);
  });

  it("returns true when the file was modified more than quietMs ago", () => {
    const now = 1_000_000;
    const lastModified = now - 10 * 60 * 1000;
    expect(isQuietPeriodElapsed(lastModified, now, 5 * 60 * 1000)).toBe(true);
  });
});

// ─── buildPendingQueue ────────────────────────────────────────────────────────

describe("buildPendingQueue", () => {
  it("includes files that changed since last sync (different hash)", () => {
    const s = makeSession();
    const syncState: SyncState = {
      lastSyncedHash: { "/path/to/file.jsonl": "old-hash" },
      syncErrors: {},
      lastRunAt: null,
      queueLength: 0,
    };
    const now = 1_000_000;
    const files: PendingFile[] = [
      {
        filePath: "/path/to/file.jsonl",
        session: s,
        lastModifiedAt: now - 10 * 60 * 1000, // old enough
      },
    ];

    const queue = buildPendingQueue(files, syncState, BASE_SETTINGS, now);
    expect(queue).toHaveLength(1);
    expect(queue[0].filePath).toBe("/path/to/file.jsonl");
  });

  it("excludes files that are within the quiet period (modified < 5min ago)", () => {
    const s = makeSession();
    const syncState: SyncState = {
      lastSyncedHash: {},
      syncErrors: {},
      lastRunAt: null,
      queueLength: 0,
    };
    const now = 1_000_000;
    const files: PendingFile[] = [
      {
        filePath: "/path/to/file.jsonl",
        session: s,
        lastModifiedAt: now - 2 * 60 * 1000, // too recent
      },
    ];

    const queue = buildPendingQueue(files, syncState, BASE_SETTINGS, now);
    expect(queue).toHaveLength(0);
  });

  it("excludes files whose hash matches lastSyncedHash (already synced)", () => {
    const s = makeSession();
    const hash = computeSessionHash(s);
    const syncState: SyncState = {
      lastSyncedHash: { "/path/to/file.jsonl": hash },
      syncErrors: {},
      lastRunAt: null,
      queueLength: 0,
    };
    const now = 1_000_000;
    const files: PendingFile[] = [
      {
        filePath: "/path/to/file.jsonl",
        session: s,
        lastModifiedAt: now - 10 * 60 * 1000,
      },
    ];

    const queue = buildPendingQueue(files, syncState, BASE_SETTINGS, now);
    expect(queue).toHaveLength(0);
  });

  it("excludes files from tools not in autoSyncTools", () => {
    const s = makeSession({ tool: "kilo-code" });
    const syncState: SyncState = {
      lastSyncedHash: {},
      syncErrors: {},
      lastRunAt: null,
      queueLength: 0,
    };
    const now = 1_000_000;
    const files: PendingFile[] = [
      {
        filePath: "/path/to/file.jsonl",
        session: s,
        lastModifiedAt: now - 10 * 60 * 1000,
      },
    ];

    // autoSyncTools only has claude-code + codex, not kilo-code
    const queue = buildPendingQueue(files, syncState, BASE_SETTINGS, now);
    expect(queue).toHaveLength(0);
  });

  it("excludes files when syncMode is manual", () => {
    const s = makeSession();
    const syncState: SyncState = {
      lastSyncedHash: {},
      syncErrors: {},
      lastRunAt: null,
      queueLength: 0,
    };
    const now = 1_000_000;
    const files: PendingFile[] = [
      {
        filePath: "/path/to/file.jsonl",
        session: s,
        lastModifiedAt: now - 10 * 60 * 1000,
      },
    ];

    const queue = buildPendingQueue(
      files,
      syncState,
      { ...BASE_SETTINGS, syncMode: "manual" },
      now,
    );
    expect(queue).toHaveLength(0);
  });
});

// ─── processSyncQueue ─────────────────────────────────────────────────────────

describe("processSyncQueue", () => {
  it("calls push for each item in the queue and updates lastSyncedHash", async () => {
    const s = makeSession();
    const pushSpy = vi.fn().mockResolvedValue({ id: s.id });
    const markPushedSpy = vi.fn();
    const setStateSpy = vi.fn();

    const deps: AutoSyncDeps = {
      push: pushSpy,
      markPushed: markPushedSpy,
      setSyncState: setStateSpy,
      redactSession: (sess) => ({ session: sess, count: 0 }),
    };

    const queue: PendingFile[] = [
      {
        filePath: s.filePath!,
        session: s,
        lastModifiedAt: 0,
      },
    ];

    const initialState: SyncState = {
      lastSyncedHash: {},
      syncErrors: {},
      lastRunAt: null,
      queueLength: 0,
    };

    await processSyncQueue(queue, BASE_SETTINGS, initialState, deps);

    expect(pushSpy).toHaveBeenCalledOnce();
    expect(markPushedSpy).toHaveBeenCalledWith(s.id);

    // setSyncState should be called with updated hash
    const calledWith = setStateSpy.mock.calls[setStateSpy.mock.calls.length - 1][0];
    expect(calledWith.lastSyncedHash[s.filePath!]).toBe(computeSessionHash(s));
    expect(calledWith.lastRunAt).not.toBeNull();
    expect(calledWith.queueLength).toBe(0);
  });

  it("records error in syncErrors when push fails", async () => {
    const s = makeSession();
    const pushSpy = vi.fn().mockRejectedValue(new Error("network error"));
    const markPushedSpy = vi.fn();
    const setStateSpy = vi.fn();

    const deps: AutoSyncDeps = {
      push: pushSpy,
      markPushed: markPushedSpy,
      setSyncState: setStateSpy,
      redactSession: (sess) => ({ session: sess, count: 0 }),
    };

    const queue: PendingFile[] = [
      {
        filePath: s.filePath!,
        session: s,
        lastModifiedAt: 0,
      },
    ];

    const initialState: SyncState = {
      lastSyncedHash: {},
      syncErrors: {},
      lastRunAt: null,
      queueLength: 0,
    };

    await processSyncQueue(queue, BASE_SETTINGS, initialState, deps);

    expect(markPushedSpy).not.toHaveBeenCalled();
    const calledWith = setStateSpy.mock.calls[setStateSpy.mock.calls.length - 1][0];
    expect(calledWith.syncErrors[s.filePath!]).toContain("network error");
  });

  it("applies redaction when redactBeforePush is true", async () => {
    const s = makeSession();
    const redactSpy = vi.fn().mockReturnValue({ session: { ...s, preview: "REDACTED" }, count: 1 });
    const pushSpy = vi.fn().mockResolvedValue({ id: s.id });

    const deps: AutoSyncDeps = {
      push: pushSpy,
      markPushed: vi.fn(),
      setSyncState: vi.fn(),
      redactSession: redactSpy,
    };

    const queue: PendingFile[] = [
      {
        filePath: s.filePath!,
        session: s,
        lastModifiedAt: 0,
      },
    ];

    await processSyncQueue(
      queue,
      { ...BASE_SETTINGS, redactBeforePush: true },
      { lastSyncedHash: {}, syncErrors: {}, lastRunAt: null, queueLength: 0 },
      deps,
    );

    expect(redactSpy).toHaveBeenCalledOnce();
    // push should receive the redacted session
    const envelope = pushSpy.mock.calls[0][0];
    expect(envelope.session.preview).toBe("REDACTED");
  });

  it("does NOT apply redaction when redactBeforePush is false", async () => {
    const s = makeSession();
    const redactSpy = vi.fn().mockReturnValue({ session: s, count: 0 });
    const pushSpy = vi.fn().mockResolvedValue({ id: s.id });

    const deps: AutoSyncDeps = {
      push: pushSpy,
      markPushed: vi.fn(),
      setSyncState: vi.fn(),
      redactSession: redactSpy,
    };

    const queue: PendingFile[] = [
      { filePath: s.filePath!, session: s, lastModifiedAt: 0 },
    ];

    await processSyncQueue(
      queue,
      { ...BASE_SETTINGS, redactBeforePush: false },
      { lastSyncedHash: {}, syncErrors: {}, lastRunAt: null, queueLength: 0 },
      deps,
    );

    expect(redactSpy).not.toHaveBeenCalled();
  });
});

// ─── reconcilePushedIds ───────────────────────────────────────────────────────

describe("reconcilePushedIds", () => {
  it("removes pushedIds that are not in the hub response", () => {
    const localIds = ["sess-1", "sess-2", "sess-3"];
    const hubIds = new Set(["sess-1", "sess-3"]);
    const result = reconcilePushedIds(localIds, hubIds);
    expect(result).toEqual(["sess-1", "sess-3"]);
    expect(result).not.toContain("sess-2");
  });

  it("keeps all pushedIds that are confirmed on the hub", () => {
    const localIds = ["sess-a", "sess-b"];
    const hubIds = new Set(["sess-a", "sess-b", "sess-c"]);
    const result = reconcilePushedIds(localIds, hubIds);
    expect(result).toEqual(["sess-a", "sess-b"]);
  });

  it("returns empty array when none match", () => {
    const result = reconcilePushedIds(["x", "y"], new Set(["a", "b"]));
    expect(result).toEqual([]);
  });
});
