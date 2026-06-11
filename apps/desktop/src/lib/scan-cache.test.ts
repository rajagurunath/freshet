/**
 * Tests for scan-cache.ts — incremental file scan cache.
 *
 * The cache records {mtime, size, sessionId} per filePath so scanLocalSessions
 * can skip unchanged files and only re-parse modified or new ones.
 */
import { describe, expect, it } from "vitest";
import {
  buildScanCache,
  mergeScanResult,
  isCacheEntryStale,
  type ScanCache,
  type ScanCacheEntry,
  type ScanFileInfo,
} from "./scan-cache";
import type { NormalizedSession } from "./types";

// ─── helpers ─────────────────────────────────────────────────────────────────

function makeSession(id: string, filePath: string): NormalizedSession {
  return {
    id,
    tool: "claude-code",
    title: `Session ${id}`,
    messageCount: 2,
    messages: [],
    models: [],
    preview: "test",
    filePath,
  };
}

function makeEntry(overrides?: Partial<ScanCacheEntry>): ScanCacheEntry {
  return {
    mtime: 1000,
    size: 512,
    sessionId: "sess-1",
    ...overrides,
  };
}

// ─── isCacheEntryStale ────────────────────────────────────────────────────────

describe("isCacheEntryStale", () => {
  it("returns false when mtime and size match", () => {
    const entry = makeEntry({ mtime: 1000, size: 512 });
    const info: ScanFileInfo = { mtime: 1000, size: 512 };
    expect(isCacheEntryStale(entry, info)).toBe(false);
  });

  it("returns true when mtime differs", () => {
    const entry = makeEntry({ mtime: 1000, size: 512 });
    const info: ScanFileInfo = { mtime: 2000, size: 512 };
    expect(isCacheEntryStale(entry, info)).toBe(true);
  });

  it("returns true when size differs", () => {
    const entry = makeEntry({ mtime: 1000, size: 512 });
    const info: ScanFileInfo = { mtime: 1000, size: 1024 };
    expect(isCacheEntryStale(entry, info)).toBe(true);
  });

  it("returns true when both mtime and size differ", () => {
    const entry = makeEntry({ mtime: 1000, size: 512 });
    const info: ScanFileInfo = { mtime: 9999, size: 9999 };
    expect(isCacheEntryStale(entry, info)).toBe(true);
  });

  it("returns true when entry is undefined (new file)", () => {
    const info: ScanFileInfo = { mtime: 1000, size: 512 };
    expect(isCacheEntryStale(undefined, info)).toBe(true);
  });
});

// ─── buildScanCache ───────────────────────────────────────────────────────────

describe("buildScanCache", () => {
  it("builds a cache from a list of sessions + their file infos", () => {
    const sessions = [
      makeSession("sess-1", "/home/user/session1.jsonl"),
      makeSession("sess-2", "/home/user/session2.jsonl"),
    ];
    const fileInfos: Record<string, ScanFileInfo> = {
      "/home/user/session1.jsonl": { mtime: 1000, size: 100 },
      "/home/user/session2.jsonl": { mtime: 2000, size: 200 },
    };

    const cache = buildScanCache(sessions, fileInfos);

    expect(cache["/home/user/session1.jsonl"]).toEqual({
      mtime: 1000,
      size: 100,
      sessionId: "sess-1",
    });
    expect(cache["/home/user/session2.jsonl"]).toEqual({
      mtime: 2000,
      size: 200,
      sessionId: "sess-2",
    });
  });

  it("skips sessions with no filePath", () => {
    // Use a session where filePath is an empty string (falsy)
    const sessions = [makeSession("sess-1", "")];
    const cache = buildScanCache(sessions, {});
    expect(Object.keys(cache)).toHaveLength(0);
  });

  it("returns an empty cache when given empty inputs", () => {
    expect(buildScanCache([], {})).toEqual({});
  });
});

// ─── mergeScanResult ──────────────────────────────────────────────────────────

describe("mergeScanResult", () => {
  it("returns freshly-parsed sessions verbatim when cache is empty", () => {
    const parsed = [makeSession("new-1", "/tmp/new1.jsonl")];
    const fileInfos: Record<string, ScanFileInfo> = {
      "/tmp/new1.jsonl": { mtime: 100, size: 50 },
    };
    const { sessions, updatedCache } = mergeScanResult([], parsed, {}, fileInfos);

    expect(sessions).toHaveLength(1);
    expect(sessions[0].id).toBe("new-1");
    expect(updatedCache["/tmp/new1.jsonl"]).toEqual({ mtime: 100, size: 50, sessionId: "new-1" });
  });

  it("re-uses cached sessions whose files are unchanged", () => {
    const existing = makeSession("sess-cached", "/tmp/cached.jsonl");
    const cache: ScanCache = {
      "/tmp/cached.jsonl": { mtime: 500, size: 256, sessionId: "sess-cached" },
    };
    const fileInfos: Record<string, ScanFileInfo> = {
      "/tmp/cached.jsonl": { mtime: 500, size: 256 },
    };

    // parsedForChanged is empty — file was not re-parsed
    const { sessions, updatedCache } = mergeScanResult([existing], [], cache, fileInfos);

    expect(sessions).toHaveLength(1);
    expect(sessions[0].id).toBe("sess-cached");
    // Cache entry must be retained
    expect(updatedCache["/tmp/cached.jsonl"]).toEqual({
      mtime: 500,
      size: 256,
      sessionId: "sess-cached",
    });
  });

  it("replaces old session with freshly-parsed one when file changed", () => {
    const stale = makeSession("sess-old", "/tmp/changed.jsonl");
    const fresh = makeSession("sess-new", "/tmp/changed.jsonl");

    const cache: ScanCache = {
      "/tmp/changed.jsonl": { mtime: 100, size: 50, sessionId: "sess-old" },
    };
    const fileInfos: Record<string, ScanFileInfo> = {
      "/tmp/changed.jsonl": { mtime: 999, size: 80 },
    };

    const { sessions, updatedCache } = mergeScanResult(
      [stale],
      [fresh],
      cache,
      fileInfos,
    );

    expect(sessions).toHaveLength(1);
    expect(sessions[0].id).toBe("sess-new");
    expect(updatedCache["/tmp/changed.jsonl"]).toEqual({
      mtime: 999,
      size: 80,
      sessionId: "sess-new",
    });
  });

  it("prunes cache entries for files that are no longer present", () => {
    const removed = makeSession("sess-gone", "/tmp/removed.jsonl");
    const cache: ScanCache = {
      "/tmp/removed.jsonl": { mtime: 100, size: 50, sessionId: "sess-gone" },
    };
    // File is no longer in fileInfos (it was deleted)
    const fileInfos: Record<string, ScanFileInfo> = {};

    const { sessions, updatedCache } = mergeScanResult([removed], [], cache, fileInfos);

    // Session should be dropped
    expect(sessions).toHaveLength(0);
    // Cache entry for the removed file should be pruned
    expect(updatedCache["/tmp/removed.jsonl"]).toBeUndefined();
  });

  it("merges unchanged + changed + new files correctly", () => {
    const unchanged = makeSession("sess-unchanged", "/tmp/unchanged.jsonl");
    const freshChanged = makeSession("sess-changed-new", "/tmp/changed.jsonl");
    const fresh = makeSession("sess-brand-new", "/tmp/new.jsonl");

    const cache: ScanCache = {
      "/tmp/unchanged.jsonl": { mtime: 100, size: 50, sessionId: "sess-unchanged" },
      "/tmp/changed.jsonl": { mtime: 100, size: 50, sessionId: "sess-changed-old" },
    };
    const fileInfos: Record<string, ScanFileInfo> = {
      "/tmp/unchanged.jsonl": { mtime: 100, size: 50 },   // unchanged
      "/tmp/changed.jsonl": { mtime: 999, size: 100 },    // changed
      "/tmp/new.jsonl": { mtime: 777, size: 30 },          // new
    };

    const { sessions, updatedCache } = mergeScanResult(
      [unchanged],
      [freshChanged, fresh],
      cache,
      fileInfos,
    );

    expect(sessions).toHaveLength(3);
    const ids = sessions.map((s) => s.id);
    expect(ids).toContain("sess-unchanged");
    expect(ids).toContain("sess-changed-new");
    expect(ids).toContain("sess-brand-new");

    expect(updatedCache["/tmp/unchanged.jsonl"].sessionId).toBe("sess-unchanged");
    expect(updatedCache["/tmp/changed.jsonl"].sessionId).toBe("sess-changed-new");
    expect(updatedCache["/tmp/new.jsonl"].sessionId).toBe("sess-brand-new");
  });
});

// ─── needsParse (regression: empty sessions after app restart) ────────────────
//
// scanCache is persisted across app launches but parsed sessions are not.
// A cache-fresh file with no in-memory session MUST still be parsed, or every
// restart shows an empty sessions list until a file changes on disk.

import { needsParse } from "./scan-cache";

describe("needsParse", () => {
  const entry = { mtime: 100, size: 50, sessionId: "s1" };
  const sameInfo = { mtime: 100, size: 50 };
  const changedInfo = { mtime: 999, size: 50 };

  it("parses a cache-fresh file when no previous session exists (app restart)", () => {
    expect(needsParse(entry, sameInfo, false)).toBe(true);
  });

  it("skips a cache-fresh file when the session is already in memory", () => {
    expect(needsParse(entry, sameInfo, true)).toBe(false);
  });

  it("parses a changed file even when a previous session exists", () => {
    expect(needsParse(entry, changedInfo, true)).toBe(true);
  });

  it("parses an uncached file", () => {
    expect(needsParse(undefined, sameInfo, false)).toBe(true);
  });
});
