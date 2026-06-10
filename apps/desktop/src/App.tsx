import React, { useEffect, useState } from "react";
import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import {
  LayoutDashboard,
  TerminalSquare,
  Globe,
  MessageCircle,
  Settings,
  RefreshCw,
  CheckCircle2,
} from "lucide-react";
import { cn } from "@/components/ui/cn";
import { ToastProvider } from "@/components/ui/Toast";
import { Tooltip } from "@/components/ui/Tooltip";
import { DashboardPage } from "@/pages/DashboardPage";
import { SessionsPage } from "@/pages/SessionsPage";
import { SessionDetailPage } from "@/pages/SessionDetailPage";
import { HubPage } from "@/pages/HubPage";
import { AgentPage } from "@/pages/AgentPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { useApp } from "@/store/app";
import { useSettings } from "@/store/settings";
import { makeApiClient } from "@/lib/api/client";
import { startAutoSync, reconcilePushedIds, type AutoSyncDeps } from "@/lib/autosync";
import { redactSession } from "@/lib/redact";
import { getSessionRoots, startWatching, statFile } from "@/lib/tauri";

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/sessions", label: "My Sessions", icon: TerminalSquare },
  { to: "/hub", label: "Company Hub", icon: Globe },
  { to: "/agent", label: "Ask the Agent", icon: MessageCircle },
] as const;

type HealthState = "unknown" | "ok" | "error";

function AppShell() {
  const {
    loadSessions,
    rescan,
    sessions,
    pushedIds,
    syncState,
    setSyncState,
    markPushed,
    reconcilePushed,
  } = useApp();
  const settings = useSettings();
  const [health, setHealth] = useState<HealthState>("unknown");

  // Load local sessions on mount
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Re-scan on window focus (incremental — only parses new/changed files)
  useEffect(() => {
    const onFocus = () => { void rescan(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [rescan]);

  // ── Auto-sync engine ──────────────────────────────────────────────────────
  useEffect(() => {
    if (settings.syncMode !== "auto") return;

    // Start Tauri watcher (no-op in browser mode)
    void getSessionRoots().then((roots) => {
      if (roots.length > 0) {
        void startWatching(roots).catch(() => {
          // Watcher is best-effort; silence failures
        });
      }
    });

    const deps: AutoSyncDeps = {
      push: async (envelope) => {
        const client = makeApiClient(settings.apiBaseUrl, settings.apiKey);
        return client.pushSession(envelope);
      },
      markPushed,
      setSyncState,
      redactSession,
    };

    const getFileInfos = async (): Promise<Record<string, { mtime: number; size: number }>> => {
      const infos: Record<string, { mtime: number; size: number }> = {};
      await Promise.all(
        sessions
          .filter((s) => s.filePath)
          .map(async (s) => {
            try {
              const info = await statFile(s.filePath!);
              infos[s.filePath!] = info;
            } catch {
              // File may be gone; skip
            }
          }),
      );
      return infos;
    };

    const stop = startAutoSync(
      () => ({
        syncMode: settings.syncMode,
        autoSyncTools: settings.autoSyncTools,
        redactBeforePush: settings.redactBeforePush,
        defaultCategory: settings.defaultCategory,
        defaultVisibility: settings.defaultVisibility,
        apiBaseUrl: settings.apiBaseUrl,
        apiKey: settings.apiKey,
        author: settings.author,
      }),
      () => syncState,
      deps,
      () => sessions,
      getFileInfos,
    );

    return stop;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.syncMode, settings.autoSyncTools.join(",")]);

  // ── Reconcile pushedIds when connected ───────────────────────────────────
  useEffect(() => {
    if (!settings.apiBaseUrl || pushedIds.length === 0) return;
    void (async () => {
      try {
        const client = makeApiClient(settings.apiBaseUrl, settings.apiKey);
        const rows = await client.listHubSessions({
          author: settings.author.id || settings.author.email || undefined,
        });
        const hubIds = new Set(rows.map((r) => r.id));
        const reconciled = reconcilePushedIds(pushedIds, hubIds);
        if (reconciled.length !== pushedIds.length) {
          reconcilePushed(reconciled);
        }
      } catch {
        // Hub unreachable — keep local pushedIds as-is
      }
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings.apiBaseUrl, settings.apiKey]);

  // Poll API health when a baseUrl is configured
  useEffect(() => {
    if (!settings.apiBaseUrl) {
      setHealth("unknown");
      return;
    }
    let cancelled = false;

    const check = async () => {
      try {
        const client = makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "");
        await client.health();
        if (!cancelled) setHealth("ok");
      } catch {
        if (!cancelled) setHealth("error");
      }
    };

    check();
    const interval = setInterval(check, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [settings.apiBaseUrl, settings.apiKey]);

  const hostname = React.useMemo(() => {
    if (!settings.apiBaseUrl) return null;
    try {
      return new URL(settings.apiBaseUrl).hostname;
    } catch {
      return settings.apiBaseUrl;
    }
  }, [settings.apiBaseUrl]);

  return (
    <div className="flex h-full bg-bg">
      {/* Sidebar */}
      <aside className="w-[240px] shrink-0 flex flex-col border-r border-border bg-bg h-full overflow-hidden">
        {/* Brand */}
        <div className="flex items-center gap-2.5 px-5 py-5 border-b border-border">
          {/* Mark */}
          <div className="w-7 h-7 rounded-[8px] bg-accent-wash border border-accent/20 flex items-center justify-center shrink-0">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M2 4C2 3.448 2.448 3 3 3h4v4H2V4zM2 9h5v4H3c-.552 0-1-.448-1-1V9zM9 3h4c.552 0 1 .448 1 1v4H9V3zM14 9v3c0 .552-.448 1-1 1H9V9h5z"
                fill="#F2541B"
              />
            </svg>
          </div>
          <span className="text-body font-semibold text-ink tracking-tight">Context Hub</span>
        </div>

        {/* Navigation */}
        <nav className="flex flex-col gap-0.5 p-2 flex-1">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 px-3 py-2 rounded-[8px] text-small font-medium transition-all duration-120",
                  isActive
                    ? "bg-accent-wash text-accent-ink border-l-2 border-accent pl-[10px]"
                    : "text-ink-soft hover:bg-bg-sunken hover:text-ink",
                )
              }
            >
              <Icon size={16} strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}

          {/* Spacer */}
          <div className="flex-1" />

          {/* Settings at bottom */}
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 px-3 py-2 rounded-[8px] text-small font-medium transition-all duration-120 mt-1",
                isActive
                  ? "bg-accent-wash text-accent-ink border-l-2 border-accent pl-[10px]"
                  : "text-ink-soft hover:bg-bg-sunken hover:text-ink",
              )
            }
          >
            <Settings size={16} strokeWidth={1.75} />
            Settings
          </NavLink>
        </nav>

        {/* Footer: sync mode + health */}
        <div className="p-3 border-t border-border space-y-2">
          {/* Sync mode pill */}
          <div className="flex items-center gap-2 px-2 py-1">
            <RefreshCw
              size={12}
              className={cn(
                settings.syncMode === "auto" ? "text-accent" : "text-ink-faint",
              )}
            />
            <span className="text-micro text-ink-faint">
              {settings.syncMode === "auto" ? "Auto sync" : "Manual sync"}
            </span>
            <span
              className={cn(
                "ml-auto text-micro px-1.5 py-0.5 rounded-full font-medium",
                settings.syncMode === "auto"
                  ? "bg-accent-wash text-accent-ink"
                  : "bg-bg-sunken text-ink-faint",
              )}
            >
              {settings.syncMode === "auto"
                ? syncState.queueLength > 0
                  ? `${syncState.queueLength} queued`
                  : "auto"
                : "manual"}
            </span>
          </div>

          {/* Connection status */}
          {hostname && (
            <Tooltip
              content={
                health === "ok"
                  ? "Connected"
                  : health === "error"
                  ? "Cannot reach hub"
                  : "Checking…"
              }
              side="right"
            >
              <div className="flex items-center gap-2 px-2 py-1 cursor-default">
                <div
                  className={cn(
                    "w-2 h-2 rounded-full shrink-0",
                    health === "ok" && "bg-success",
                    health === "error" && "bg-danger",
                    health === "unknown" && "bg-border-strong",
                  )}
                />
                <span className="text-micro text-ink-faint truncate">{hostname}</span>
              </div>
            </Tooltip>
          )}

          {/* Session count */}
          {sessions.length > 0 && (
            <div className="flex items-center gap-2 px-2">
              <CheckCircle2 size={11} className="text-ink-faint" />
              <span className="text-micro text-ink-faint">
                {sessions.length} local sessions
              </span>
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 flex flex-col h-full overflow-hidden">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/sessions/:id" element={<SessionDetailPage />} />
          <Route path="/hub" element={<HubPage />} />
          <Route path="/agent" element={<AgentPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppShell />
    </ToastProvider>
  );
}
