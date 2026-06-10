/**
 * Persistent settings store (zustand + localStorage).
 * Holds API credentials, author info, and sync preferences.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Author, Category, Tool, Visibility } from "../lib/types";

export interface SettingsState {
  // ── API connection ──────────────────────────────────────────────────────
  apiBaseUrl: string;
  apiKey: string;

  // ── user identity ───────────────────────────────────────────────────────
  author: Author;

  // ── sync preferences ────────────────────────────────────────────────────
  syncMode: "manual" | "auto";
  autoSyncTools: Tool[];
  defaultCategory: Category;
  defaultVisibility: Visibility;
  redactBeforePush: boolean;

  // ── AI provider ─────────────────────────────────────────────────────────
  /** claude-cli | codex-cli | anthropic | openai */
  llmProvider: string;
  llmModel: string;
  /** Whether the user has consented to Context Hub using their coding agent. */
  aiConsent: boolean;

  // ── setters ─────────────────────────────────────────────────────────────
  setApiBaseUrl: (url: string) => void;
  setApiKey: (key: string) => void;
  setAuthor: (author: Partial<Author>) => void;
  setSyncMode: (mode: "manual" | "auto") => void;
  setAutoSyncTools: (tools: Tool[]) => void;
  setDefaultCategory: (cat: Category) => void;
  setDefaultVisibility: (vis: Visibility) => void;
  setRedactBeforePush: (v: boolean) => void;
  /**
   * Generic partial update — convenience setter for pages that want to
   * update multiple fields at once.
   */
  update: (patch: Partial<Omit<SettingsState, "update" | "setApiBaseUrl" | "setApiKey" | "setAuthor" | "setSyncMode" | "setAutoSyncTools" | "setDefaultCategory" | "setDefaultVisibility" | "setRedactBeforePush">>) => void;
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      // defaults
      apiBaseUrl: "http://localhost:8787",
      apiKey: "dev-key",
      author: { id: "", email: "", name: "" },
      syncMode: "manual",
      autoSyncTools: [],
      defaultCategory: "engineering",
      defaultVisibility: "company",
      redactBeforePush: true,
      llmProvider: "claude-cli",
      llmModel: "sonnet",
      aiConsent: false,

      // setters
      setApiBaseUrl: (url) => set({ apiBaseUrl: url }),
      setApiKey: (key) => set({ apiKey: key }),
      setAuthor: (author) =>
        set((state) => ({ author: { ...state.author, ...author } })),
      setSyncMode: (mode) => set({ syncMode: mode }),
      setAutoSyncTools: (tools) => set({ autoSyncTools: tools }),
      setDefaultCategory: (cat) => set({ defaultCategory: cat }),
      setDefaultVisibility: (vis) => set({ defaultVisibility: vis }),
      setRedactBeforePush: (v) => set({ redactBeforePush: v }),
      update: (patch) => set(patch),
    }),
    {
      name: "context-hub-settings",
      // Exclude function keys from persistence
      partialize: (state) => ({
        apiBaseUrl: state.apiBaseUrl,
        apiKey: state.apiKey,
        author: state.author,
        syncMode: state.syncMode,
        autoSyncTools: state.autoSyncTools,
        defaultCategory: state.defaultCategory,
        defaultVisibility: state.defaultVisibility,
        redactBeforePush: state.redactBeforePush,
        llmProvider: state.llmProvider,
        llmModel: state.llmModel,
        aiConsent: state.aiConsent,
      }),
    }
  )
);
