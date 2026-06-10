/**
 * Incremental file scan cache.
 *
 * Records {mtime, size, sessionId} per filePath so that scanLocalSessions can
 * skip unchanged files and only re-parse modified or brand-new ones.  The
 * cache is kept in localStorage via Zustand persist and is a plain JSON object
 * so it survives serialization round-trips.
 *
 * Contracts
 * ---------
 * - `ScanCache` is the shape persisted in the store.
 * - `ScanFileInfo` is what the file-system layer provides about a discovered
 *   file (mtime as Unix ms, byte-size).
 * - `isCacheEntryStale` is the single decision point: does a file need
 *   re-parsing?
 * - `buildScanCache` rebuilds the cache from a completed scan result.
 * - `mergeScanResult` combines the previous-session list with freshly-parsed
 *   sessions, applying the cache to avoid work.
 */

import type { NormalizedSession } from "./types";

// ─── types ────────────────────────────────────────────────────────────────────

/** File-system metadata snapshot for a single session file. */
export interface ScanFileInfo {
  /** Last-modified time in milliseconds since Unix epoch. */
  mtime: number;
  /** File byte-size. */
  size: number;
}

/** One entry in the persisted scan cache. */
export interface ScanCacheEntry extends ScanFileInfo {
  /** The session id that was produced when this file was last parsed. */
  sessionId: string;
}

/** Persisted scan cache: filePath → entry. */
export type ScanCache = Record<string, ScanCacheEntry>;

// ─── isCacheEntryStale ────────────────────────────────────────────────────────

/**
 * Returns true if the file described by `info` differs from the cached entry
 * (or if there is no cached entry), meaning it needs to be re-parsed.
 */
export function isCacheEntryStale(
  entry: ScanCacheEntry | undefined,
  info: ScanFileInfo,
): boolean {
  if (!entry) return true;
  return entry.mtime !== info.mtime || entry.size !== info.size;
}

// ─── buildScanCache ───────────────────────────────────────────────────────────

/**
 * Build a fresh `ScanCache` from the results of a complete scan.
 *
 * @param sessions  All sessions returned by the scan (each must have filePath).
 * @param fileInfos Mapping of filePath → { mtime, size } as reported by the FS.
 */
export function buildScanCache(
  sessions: NormalizedSession[],
  fileInfos: Record<string, ScanFileInfo>,
): ScanCache {
  const cache: ScanCache = {};
  for (const session of sessions) {
    const fp = session.filePath;
    if (!fp) continue;
    const info = fileInfos[fp];
    if (!info) continue;
    cache[fp] = { mtime: info.mtime, size: info.size, sessionId: session.id };
  }
  return cache;
}

// ─── mergeScanResult ──────────────────────────────────────────────────────────

/** Result returned from mergeScanResult. */
export interface MergeScanResult {
  /** Final merged session list (unchanged + freshly-parsed). */
  sessions: NormalizedSession[];
  /** Updated cache ready to be persisted. */
  updatedCache: ScanCache;
}

/**
 * Merge the previous session list with freshly-parsed sessions.
 *
 * Algorithm
 * ---------
 * 1. Build a map of filePath → freshly-parsed session (for O(1) lookup).
 * 2. For every file currently present on disk (in `fileInfos`):
 *    a. If there is a freshly-parsed session for it → use that (new or
 *       changed file).
 *    b. Otherwise (file unchanged) → look up the existing session by the
 *       sessionId stored in the cache and carry it forward.
 * 3. Files that are in `previousSessions` but NOT in `fileInfos` have been
 *    deleted — they are silently dropped and their cache entry is pruned.
 * 4. Build the updated cache from the final session list + fileInfos.
 *
 * @param previousSessions  Sessions from the last scan (may be stale).
 * @param parsedForChanged  Freshly-parsed sessions for new/changed files.
 * @param previousCache     The persisted cache from the last scan.
 * @param fileInfos         Current FS metadata for all discovered files.
 */
export function mergeScanResult(
  previousSessions: NormalizedSession[],
  parsedForChanged: NormalizedSession[],
  previousCache: ScanCache,
  fileInfos: Record<string, ScanFileInfo>,
): MergeScanResult {
  // Index previous sessions by filePath for O(1) lookup.
  const prevByFilePath = new Map<string, NormalizedSession>();
  for (const s of previousSessions) {
    if (s.filePath) prevByFilePath.set(s.filePath, s);
  }

  // Index freshly-parsed sessions by filePath.
  const freshByFilePath = new Map<string, NormalizedSession>();
  for (const s of parsedForChanged) {
    if (s.filePath) freshByFilePath.set(s.filePath, s);
  }

  const sessions: NormalizedSession[] = [];
  const updatedCache: ScanCache = {};

  // Iterate over all currently-present files.
  for (const [fp, info] of Object.entries(fileInfos)) {
    const fresh = freshByFilePath.get(fp);
    if (fresh) {
      // File was new or changed — use the freshly-parsed session.
      sessions.push(fresh);
      updatedCache[fp] = { mtime: info.mtime, size: info.size, sessionId: fresh.id };
    } else {
      // File is unchanged — carry forward the existing session.
      const prev = prevByFilePath.get(fp);
      if (prev) {
        sessions.push(prev);
        updatedCache[fp] = {
          mtime: info.mtime,
          size: info.size,
          sessionId: prev.id,
        };
      }
      // If neither fresh nor prev exists for a file, skip it (could happen if
      // the file existed when we recorded fileInfos but wasn't parsed at all).
    }
  }

  return { sessions, updatedCache };
}
