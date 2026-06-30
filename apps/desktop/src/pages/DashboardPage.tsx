import React, { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  MessagesSquare,
  Wrench,
  Zap,
  FolderGit2,
  Cpu,
  UploadCloud,
  TerminalSquare,
} from "lucide-react";
import { Card } from "@/components/ui/Card";
import { StatTile } from "@/components/StatTile";
import { BarList, type BarItem } from "@/components/BarList";
import { EmptyState } from "@/components/ui/EmptyState";
import { ToolChip } from "@/components/ToolChip";
import { useApp } from "@/store/app";
import { dashboardStats } from "@/lib/aggregate";
import { TOOL_LABELS, type Tool } from "@/lib/types";

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

const toolBar: Record<Tool, string> = {
  "claude-code": "bg-accent",
  codex: "bg-ink-faint",
  "kilo-code": "bg-[#15807d]",
};

function SectionCard({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="p-5 flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className="text-ink-faint">{icon}</span>
        <h2 className="text-h3 font-semibold text-ink">{title}</h2>
      </div>
      {children}
    </Card>
  );
}

export function DashboardPage() {
  const navigate = useNavigate();
  const { sessions, loading, pushedIds } = useApp();
  const stats = useMemo(() => dashboardStats(sessions), [sessions]);

  const pushedCount = Array.isArray(pushedIds)
    ? pushedIds.length
    : (pushedIds as Set<string>)?.size ?? 0;

  if (!loading && sessions.length === 0) {
    return (
      <div className="flex flex-col h-full">
        <Header />
        <div className="flex-1">
          <EmptyState
            icon={<LayoutDashboard size={40} strokeWidth={1.25} />}
            headline="No sessions to analyze yet"
            body="Once Freshet finds your local AI coding sessions, your KPIs will appear here."
          />
        </div>
      </div>
    );
  }

  const toolBars: BarItem[] = stats.byTool.map((t) => ({
    label: TOOL_LABELS[t.key],
    value: t.count,
    barClass: toolBar[t.key],
  }));

  const tokenBars: BarItem[] = stats.tokensByTool.map((t) => ({
    label: TOOL_LABELS[t.key],
    value: t.count,
    display: formatTokens(t.count),
    barClass: toolBar[t.key],
  }));

  const toolUsageBars: BarItem[] = stats.topTools.map((t) => ({
    label: <span className="font-mono">{t.key}</span>,
    value: t.count,
  }));

  const projectBars: BarItem[] = stats.topProjects.map((p) => ({
    label: p.key,
    value: p.count,
  }));

  return (
    <div className="flex flex-col h-full">
      <Header />
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6 max-w-content">
        {/* Headline KPIs */}
        <div className="grid grid-cols-4 gap-4">
          <StatTile
            label="Sessions"
            value={stats.totalSessions}
            icon={<TerminalSquare size={16} />}
            trend={`${pushedCount} pushed`}
          />
          <StatTile
            label="Messages"
            value={stats.totalMessages}
            icon={<MessagesSquare size={16} />}
            trend={`~${stats.avgMessagesPerSession}/session`}
          />
          <StatTile
            label="Tool calls"
            value={stats.totalToolCalls}
            icon={<Wrench size={16} />}
          />
          <StatTile
            label="Tokens"
            value={formatTokens(stats.tokensTotal)}
            icon={<Zap size={16} />}
            trend={`${formatTokens(stats.tokensIn)} in · ${formatTokens(stats.tokensOut)} out`}
          />
        </div>

        {/* Breakdowns */}
        <div className="grid grid-cols-2 gap-4">
          <SectionCard title="Sessions by tool" icon={<TerminalSquare size={15} />}>
            <BarList items={toolBars} />
          </SectionCard>

          <SectionCard title="Tokens by tool" icon={<Zap size={15} />}>
            <BarList items={tokenBars} emptyText="No token data in these sessions" />
          </SectionCard>

          <SectionCard title="Most-used tools" icon={<Wrench size={15} />}>
            <BarList items={toolUsageBars} emptyText="No tool calls recorded" />
          </SectionCard>

          <SectionCard title="Top projects" icon={<FolderGit2 size={15} />}>
            <BarList items={projectBars} emptyText="No project info" />
          </SectionCard>
        </div>

        {/* Models + hub */}
        <div className="grid grid-cols-2 gap-4">
          <SectionCard title="Models used" icon={<Cpu size={15} />}>
            {stats.models.length === 0 ? (
              <p className="text-small text-ink-faint">No model info in these sessions</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {stats.models.map((m) => (
                  <span
                    key={m.key}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-bg-sunken border border-border text-small font-mono text-ink-soft"
                  >
                    {m.key}
                    <span className="text-micro text-ink-faint">×{m.count}</span>
                  </span>
                ))}
              </div>
            )}
          </SectionCard>

          <SectionCard title="Shared to Company Hub" icon={<UploadCloud size={15} />}>
            <div className="flex items-end gap-3">
              <span className="text-display font-semibold font-mono text-ink leading-none">
                {pushedCount}
              </span>
              <span className="text-small text-ink-faint mb-1">
                of {stats.totalSessions} sessions
              </span>
            </div>
            <button
              onClick={() => navigate("/hub")}
              className="self-start text-small text-accent hover:text-accent-ink transition-colors duration-120"
            >
              View Company Hub →
            </button>
          </SectionCard>
        </div>
      </div>
    </div>
  );
}

function Header() {
  return (
    <div className="px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
      <h1 className="text-h2 font-semibold text-ink">Dashboard</h1>
      <p className="text-small text-ink-faint mt-0.5">
        Your AI coding activity across every assistant, in one place.
      </p>
    </div>
  );
}
