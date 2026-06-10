/**
 * Transient + partially-persisted application state store.
 * Tracks discovered sessions and which ones have been pushed to the hub.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { NormalizedSession, Tool } from "../lib/types";
import { scanLocalSessions } from "../lib/parsers/index";

// ─── sort / filter types ──────────────────────────────────────────────────────

/** Fields on which the session list can be sorted. */
export type SortField = "date" | "project" | "tool" | "tokens" | "cost" | "messages";

/** Sort direction. */
export type SortOrder = "asc" | "desc";

/** Date-range presets for the session list filter. */
export type DateRange = "7d" | "30d" | "90d" | "all";

/** Persisted sort + filter preferences for the session list page. */
export interface SessionListPrefs {
  sortField: SortField;
  sortOrder: SortOrder;
  dateRange: DateRange;
  compactedOnly: boolean;
}

export interface AppState {
  // ── session cache ───────────────────────────────────────────────────────
  sessions: NormalizedSession[];
  loading: boolean;
  error: string | null;

  // ── pushed tracking (persisted) ─────────────────────────────────────────
  pushedIds: string[];

  // ── sort + filter prefs (persisted) ─────────────────────────────────────
  listPrefs: SessionListPrefs;

  // ── actions ─────────────────────────────────────────────────────────────
  loadSessions: () => Promise<void>;
  getSession: (id: string) => NormalizedSession | undefined;
  /** Mark a session as pushed. Alias: markAsPushed. */
  markPushed: (id: string) => void;
  markAsPushed: (id: string) => void;
  isPushed: (id: string) => boolean;
  /** Update sort + filter preferences. */
  setListPrefs: (prefs: Partial<SessionListPrefs>) => void;
}

const DEFAULT_LIST_PREFS: SessionListPrefs = {
  sortField: "date",
  sortOrder: "desc",
  dateRange: "all",
  compactedOnly: false,
};

export const useApp = create<AppState>()(
  persist(
    (set, get) => ({
      sessions: [],
      loading: false,
      error: null,
      pushedIds: [],
      listPrefs: DEFAULT_LIST_PREFS,

      loadSessions: async () => {
        set({ loading: true, error: null });
        try {
          const sessions = await scanLocalSessions();
          set({ sessions, loading: false });
        } catch (err) {
          const message =
            err instanceof Error ? err.message : "Failed to load sessions";
          set({ loading: false, error: message });
        }
      },

      getSession: (id: string) => {
        return get().sessions.find((s) => s.id === id);
      },

      markPushed: (id: string) => {
        set((state) => {
          if (state.pushedIds.includes(id)) return state;
          return { pushedIds: [...state.pushedIds, id] };
        });
      },

      markAsPushed: (id: string) => {
        set((state) => {
          if (state.pushedIds.includes(id)) return state;
          return { pushedIds: [...state.pushedIds, id] };
        });
      },

      isPushed: (id: string) => {
        return get().pushedIds.includes(id);
      },

      setListPrefs: (prefs: Partial<SessionListPrefs>) => {
        set((state) => ({
          listPrefs: { ...state.listPrefs, ...prefs },
        }));
      },
    }),
    {
      name: "context-hub-app",
      // Persist pushedIds and listPrefs; sessions & loading state are transient
      partialize: (state) => ({
        pushedIds: state.pushedIds,
        listPrefs: state.listPrefs,
      }),
    }
  )
);
