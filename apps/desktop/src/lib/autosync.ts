/**
 * Auto-sync engine (Task 10).
 *
 * Architecture
 * ------------
 * In Tauri mode a Rust `notify` watcher fires `session-file-changed` events
 * (see src-tauri/src/lib.rs `start_watching` command).  In browser/mock mode
 * the engine falls back to a 60 s poll of `listSessionFiles` mtimes.
 *
 * When a file-change event arrives:
 *   1. Check `settings.syncMode === "auto"` and the file's tool is in
 *      `autoSyncTools` — if not, skip.
 *   2. Track the last-modified timestamp per file.  Only push after the file
 *      has been stable for QUIET_PERIOD_MS (5 min) so we don't hammer the hub
 *      while the agent is still writing.
 *   3. Compute a hash over (session id + messageCount + models + preview).  If
 *      the hash is unchanged since the last push, skip (the server deduplicates
 *      via Task 2's content_hash, but skipping on the client avoids the round-
 *      trip entirely).
 *   4. Optionally redact, then push.  On success update `lastSyncedHash` and
 *      call `markPushed`.  On failure record the error in `syncErrors`.
 *
 * All I/O is injected via `AutoSyncDeps` so the logic can be unit-tested without
 * a Tauri runtime.  The actual wiring to Tauri events / the polling loop lives in
 * `startAutoSync` (production use; not unit-tested because it requires Tauri).
 */
import type { NormalizedSession, Tool, PushEnvelope, Category, Visibility } from "./types";

// ─── public types ─────────────────────────────────────────────────────────────

/** Minimal settings shape consumed by the auto-sync engine. */
export interface AutoSyncSettings {
  syncMode: "manual" | "auto";
  autoSyncTools: Tool[];
  redactBeforePush: boolean;
  defaultCategory: Category;
  defaultVisibility: Visibility;
  apiBaseUrl: string;
  apiKey: string;
  author: { id: string; email: string; name: string };
}

/** Persisted state for the auto-sync engine. */
export interface SyncState {
  /** Per-file hash of the last successfully-pushed session content. */
  lastSyncedHash: Record<string, string>;
  /** Per-file last error message (cleared on success). */
  syncErrors: Record<string, string>;
  /** ISO timestamp of the last sync run. */
  lastRunAt: string | null;
  /** Number of files currently waiting to be pushed. */
  queueLength: number;
}

/** A candidate file ready for the sync queue. */
export interface PendingFile {
  filePath: string;
  session: NormalizedSession;
  /** mtime in milliseconds (Unix epoch). */
  lastModifiedAt: number;
}

/** Injected I/O dependencies — swap out in tests. */
export interface AutoSyncDeps {
  push: (envelope: PushEnvelope) => Promise<{ id: string }>;
  markPushed: (id: string) => void;
  setSyncState: (state: SyncState) => void;
  redactSession: (s: NormalizedSession) => { session: NormalizedSession; count: number };
}

// ─── constants ────────────────────────────────────────────────────────────────

/** How long a file must be stable before it is pushed (ms). */
export const QUIET_PERIOD_MS = 5 * 60 * 1000;

/** Fallback poll interval when Tauri watcher is unavailable (ms). */
export const POLL_INTERVAL_MS = 60 * 1000;

// ─── pure logic ───────────────────────────────────────────────────────────────

/**
 * Returns true when auto-sync should be active for the given tool.
 */
export function shouldSync(settings: AutoSyncSettings, tool: Tool): boolean {
  return (
    settings.syncMode === "auto" && settings.autoSyncTools.includes(tool)
  );
}

/**
 * Compute a lightweight deterministic hash for a session so we can detect
 * changes without re-reading the file.  Uses id + messageCount + models +
 * preview (all stable once a session is "closed").
 *
 * Not cryptographically secure — collision-resistance of FNV-1a (32-bit) is
 * good enough for change detection against the same machine.
 */
export function computeSessionHash(session: NormalizedSession): string {
  const raw = [
    session.id,
    String(session.messageCount ?? 0),
    (session.models ?? []).join(","),
    session.preview ?? "",
    session.filePath ?? "",
  ].join("|");

  // FNV-1a 32-bit
  let hash = 2_166_136_261;
  for (let i = 0; i < raw.length; i++) {
    hash ^= raw.charCodeAt(i);
    hash = Math.imul(hash, 16_777_619);
    hash >>>= 0; // keep as unsigned 32-bit
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * Returns true when the quiet period has elapsed (file has been stable long
 * enough to be considered "closed" by the agent).
 */
export function isQuietPeriodElapsed(
  lastModifiedAt: number,
  now: number,
  quietMs: number = QUIET_PERIOD_MS,
): boolean {
  return now - lastModifiedAt >= quietMs;
}

/**
 * Build the list of files that should be pushed right now.
 *
 * Filters by:
 *  - syncMode + tool membership
 *  - quiet period (file must have been stable for >= quietMs)
 *  - hash deduplication (skip if hash == lastSyncedHash)
 */
export function buildPendingQueue(
  files: PendingFile[],
  syncState: SyncState,
  settings: AutoSyncSettings,
  now: number,
  quietMs: number = QUIET_PERIOD_MS,
): PendingFile[] {
  return files.filter((f) => {
    if (!shouldSync(settings, f.session.tool)) return false;
    if (!isQuietPeriodElapsed(f.lastModifiedAt, now, quietMs)) return false;
    const hash = computeSessionHash(f.session);
    if (syncState.lastSyncedHash[f.filePath] === hash) return false;
    return true;
  });
}

/**
 * Process the sync queue: push each file, update state on success, record
 * errors on failure.  Processes items sequentially to avoid hammering the hub.
 */
export async function processSyncQueue(
  queue: PendingFile[],
  settings: AutoSyncSettings,
  initialState: SyncState,
  deps: AutoSyncDeps,
): Promise<void> {
  const state: SyncState = {
    lastSyncedHash: { ...initialState.lastSyncedHash },
    syncErrors: { ...initialState.syncErrors },
    lastRunAt: initialState.lastRunAt,
    queueLength: queue.length,
  };

  // Report initial queue length
  deps.setSyncState({ ...state });

  for (const pending of queue) {
    const { filePath, session } = pending;

    // Optionally redact
    const sessionToSend = settings.redactBeforePush
      ? deps.redactSession(session).session
      : session;

    const envelope: PushEnvelope = {
      session: sessionToSend,
      author: {
        id: settings.author.id,
        email: settings.author.email,
        name: settings.author.name,
      },
      category: settings.defaultCategory,
      visibility: settings.defaultVisibility,
      redacted: settings.redactBeforePush,
    };

    try {
      const result = await deps.push(envelope);
      deps.markPushed(result.id);

      // Record the hash so we skip this file next time unless it changes
      state.lastSyncedHash[filePath] = computeSessionHash(session);
      // Clear any previous error for this file
      delete state.syncErrors[filePath];
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      state.syncErrors[filePath] = msg;
    }
  }

  state.lastRunAt = new Date().toISOString();
  state.queueLength = 0;
  deps.setSyncState({ ...state });
}

/**
 * Reconcile local `pushedIds` with the set of session ids confirmed on the hub.
 * Drops any local id that no longer exists on the hub (e.g. was deleted or the
 * hub was reset).
 */
export function reconcilePushedIds(
  localIds: string[],
  hubIds: Set<string>,
): string[] {
  return localIds.filter((id) => hubIds.has(id));
}

// ─── production wiring ────────────────────────────────────────────────────────

/**
 * Start the auto-sync engine.  In Tauri mode registers a `session-file-changed`
 * Tauri event listener (fired by the Rust `notify` watcher after debounce).  In
 * browser mode falls back to a 60 s mtime-polling interval.
 *
 * Returns a cleanup function that stops the engine.
 *
 * Note: this function is NOT unit-tested because it requires Tauri's event
 * bus and the real `listSessionFiles`/`statFile` Tauri commands.  The pure
 * logic functions above are the well-tested path.
 */
export function startAutoSync(
  getSettings: () => AutoSyncSettings,
  getSyncState: () => SyncState,
  deps: AutoSyncDeps,
  getSessions: () => NormalizedSession[],
  getFileInfos: () => Promise<Record<string, { mtime: number; size: number }>>,
): () => void {
  let stopped = false;

  async function runOnce(): Promise<void> {
    if (stopped) return;
    const settings = getSettings();
    if (settings.syncMode !== "auto") return;

    const now = Date.now();
    const syncState = getSyncState();
    const sessions = getSessions();

    // Build pending files from the current session list
    let fileInfos: Record<string, { mtime: number; size: number }> = {};
    try {
      fileInfos = await getFileInfos();
    } catch {
      return;
    }

    const pendingFiles: PendingFile[] = sessions
      .filter((s) => s.filePath)
      .map((s) => ({
        filePath: s.filePath!,
        session: s,
        lastModifiedAt: fileInfos[s.filePath!]?.mtime ?? 0,
      }));

    const queue = buildPendingQueue(pendingFiles, syncState, settings, now);
    if (queue.length === 0) return;

    await processSyncQueue(queue, settings, syncState, deps);
  }

  // Try Tauri event listener first; fall back to polling
  void (async () => {
    const isTauriEnv =
      typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

    if (isTauriEnv) {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        const unlisten = await listen<{ path: string }>(
          "session-file-changed",
          () => {
            void runOnce();
          },
        );
        // Store unlisten so cleanup works
        (cleanup as { _unlisten?: () => void })._unlisten = unlisten;
      } catch {
        // Tauri event API not available; fall through to polling
      }
    }

    // Always run a polling loop (Tauri events are debounced but polling is a
    // safe belt-and-suspenders fallback, especially in browser/dev mode).
    while (!stopped) {
      await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
      if (!stopped) await runOnce();
    }
  })();

  function cleanup() {
    stopped = true;
    const c = cleanup as { _unlisten?: () => void };
    if (c._unlisten) {
      c._unlisten();
      c._unlisten = undefined;
    }
  }

  return cleanup;
}
