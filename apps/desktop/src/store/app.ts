/**
 * Transient + partially-persisted application state store.
 * Tracks discovered sessions and which ones have been pushed to the hub.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { NormalizedSession } from "../lib/types";
import { scanLocalSessions } from "../lib/parsers/index";

export interface AppState {
  // ── session cache ───────────────────────────────────────────────────────
  sessions: NormalizedSession[];
  loading: boolean;
  error: string | null;

  // ── pushed tracking (persisted) ─────────────────────────────────────────
  pushedIds: string[];

  // ── actions ─────────────────────────────────────────────────────────────
  loadSessions: () => Promise<void>;
  getSession: (id: string) => NormalizedSession | undefined;
  /** Mark a session as pushed. Alias: markAsPushed. */
  markPushed: (id: string) => void;
  markAsPushed: (id: string) => void;
  isPushed: (id: string) => boolean;
}

export const useApp = create<AppState>()(
  persist(
    (set, get) => ({
      sessions: [],
      loading: false,
      error: null,
      pushedIds: [],

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
    }),
    {
      name: "context-hub-app",
      // Only persist pushedIds; sessions & loading state are transient
      partialize: (state) => ({
        pushedIds: state.pushedIds,
      }),
    }
  )
);
