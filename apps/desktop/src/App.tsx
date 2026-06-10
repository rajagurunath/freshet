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

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/sessions", label: "My Sessions", icon: TerminalSquare },
  { to: "/hub", label: "Company Hub", icon: Globe },
  { to: "/agent", label: "Ask the Agent", icon: MessageCircle },
] as const;

type HealthState = "unknown" | "ok" | "error";

function AppShell() {
  const { loadSessions, sessions } = useApp();
  const settings = useSettings();
  const [health, setHealth] = useState<HealthState>("unknown");

  // Load local sessions on mount
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

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
            <RefreshCw size={12} className="text-ink-faint" />
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
              {settings.syncMode ?? "manual"}
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
