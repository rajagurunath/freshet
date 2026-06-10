// Aggregation helpers: turn raw sessions into KPIs for the dashboard
// and per-session detail header. Pure functions, no side effects.
import type { NormalizedSession, Tool } from "./types";

export interface Counted<K extends string = string> {
  key: K;
  count: number;
}

/** KPIs for a single session (used by the detail header). */
export interface SessionStats {
  userMessages: number;
  assistantMessages: number;
  toolCalls: number;
  /** Tool/command names used, sorted by frequency desc. */
  toolsUsed: Counted[];
  tokensIn: number;
  tokensOut: number;
  tokensTotal: number;
  /** Wall-clock span in ms, if timestamps are available. */
  durationMs?: number;
  models: string[];
}

function toolNameOf(toolName?: string): string {
  return (toolName && toolName.trim()) || "tool";
}

export function sessionStats(s: NormalizedSession): SessionStats {
  let userMessages = 0;
  let assistantMessages = 0;
  let toolCalls = 0;
  const toolCounts = new Map<string, number>();

  for (const m of s.messages) {
    if (m.role === "user") userMessages++;
    else if (m.role === "assistant") assistantMessages++;
    if (m.role === "tool" || m.toolName) {
      toolCalls++;
      const name = toolNameOf(m.toolName);
      toolCounts.set(name, (toolCounts.get(name) ?? 0) + 1);
    }
  }

  const toolsUsed = [...toolCounts.entries()]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count);

  const tokensIn = s.tokens?.input ?? 0;
  const tokensOut = s.tokens?.output ?? 0;

  // Duration: prefer explicit start/end, else first/last message timestamps.
  const times = s.messages
    .map((m) => (m.timestamp ? Date.parse(m.timestamp) : NaN))
    .filter((t) => !Number.isNaN(t));
  const start = s.startedAt ? Date.parse(s.startedAt) : times[0];
  const end = s.endedAt ? Date.parse(s.endedAt) : times[times.length - 1];
  const durationMs =
    Number.isFinite(start) && Number.isFinite(end) && end > start
      ? end - start
      : undefined;

  return {
    userMessages,
    assistantMessages,
    toolCalls,
    toolsUsed,
    tokensIn,
    tokensOut,
    tokensTotal: tokensIn + tokensOut,
    durationMs,
    models: s.models ?? [],
  };
}

/** KPIs across a collection of sessions (used by the dashboard). */
export interface DashboardStats {
  totalSessions: number;
  totalMessages: number;
  totalToolCalls: number;
  tokensIn: number;
  tokensOut: number;
  tokensTotal: number;
  avgMessagesPerSession: number;
  byTool: Counted<Tool>[];
  tokensByTool: Counted<Tool>[];
  topProjects: Counted[];
  topTools: Counted[];
  models: Counted[];
  /** Sessions per calendar day (YYYY-MM-DD), ascending. */
  activity: { date: string; count: number }[];
}

function topN<K extends string>(map: Map<K, number>, n?: number): Counted<K>[] {
  const arr = [...map.entries()]
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count);
  return n ? arr.slice(0, n) : arr;
}

export function dashboardStats(sessions: NormalizedSession[]): DashboardStats {
  const byTool = new Map<Tool, number>();
  const tokensByTool = new Map<Tool, number>();
  const projects = new Map<string, number>();
  const tools = new Map<string, number>();
  const models = new Map<string, number>();
  const days = new Map<string, number>();

  let totalMessages = 0;
  let totalToolCalls = 0;
  let tokensIn = 0;
  let tokensOut = 0;

  for (const s of sessions) {
    byTool.set(s.tool, (byTool.get(s.tool) ?? 0) + 1);

    const tin = s.tokens?.input ?? 0;
    const tout = s.tokens?.output ?? 0;
    tokensIn += tin;
    tokensOut += tout;
    tokensByTool.set(s.tool, (tokensByTool.get(s.tool) ?? 0) + tin + tout);

    totalMessages += s.messages.length;

    if (s.project) projects.set(s.project, (projects.get(s.project) ?? 0) + 1);
    for (const mdl of s.models ?? []) models.set(mdl, (models.get(mdl) ?? 0) + 1);

    for (const m of s.messages) {
      if (m.role === "tool" || m.toolName) {
        totalToolCalls++;
        const name = toolNameOf(m.toolName);
        tools.set(name, (tools.get(name) ?? 0) + 1);
      }
    }

    const t = s.startedAt ? Date.parse(s.startedAt) : NaN;
    if (!Number.isNaN(t)) {
      const day = new Date(t).toISOString().slice(0, 10);
      days.set(day, (days.get(day) ?? 0) + 1);
    }
  }

  const activity = [...days.entries()]
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => (a.date < b.date ? -1 : 1));

  return {
    totalSessions: sessions.length,
    totalMessages,
    totalToolCalls,
    tokensIn,
    tokensOut,
    tokensTotal: tokensIn + tokensOut,
    avgMessagesPerSession: sessions.length
      ? Math.round(totalMessages / sessions.length)
      : 0,
    byTool: topN(byTool),
    tokensByTool: topN(tokensByTool),
    topProjects: topN(projects, 6),
    topTools: topN(tools, 8),
    models: topN(models),
    activity,
  };
}

/** Compact human duration: "12m", "1h 04m", "2d 3h". */
export function formatDuration(ms?: number): string {
  if (!ms || ms <= 0) return "—";
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d > 0) return `${d}d ${h % 24}h`;
  if (h > 0) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}
