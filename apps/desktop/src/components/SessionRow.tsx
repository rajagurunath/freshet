import React from "react";
import { CheckCircle2, MessageSquare, Zap } from "lucide-react";
import { cn } from "./ui/cn";
import { ToolChip } from "./ToolChip";
import type { NormalizedSession } from "@/lib/types";

// Format helpers — inline since @/lib/format may not exist yet
function relativeTimeFallback(iso?: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTokensFallback(n?: number): string {
  if (n === undefined) return "";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

interface SessionRowProps {
  session: NormalizedSession;
  pushed?: boolean;
  onClick?: () => void;
  className?: string;
}

export function SessionRow({ session, pushed = false, onClick, className }: SessionRowProps) {
  const totalTokens =
    session.tokens ? session.tokens.input + session.tokens.output : undefined;

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onClick?.()}
      className={cn(
        "flex items-center gap-4 px-5 py-3.5 border-b border-border last:border-0",
        "cursor-pointer transition-colors duration-120 hover:bg-bg-sunken",
        "focus-ring",
        className,
      )}
    >
      {/* Main content */}
      <div className="flex-1 min-w-0 flex flex-col gap-1">
        <div className="flex items-center gap-2 flex-wrap">
          <ToolChip tool={session.tool} />
          <span className="text-body font-medium text-ink truncate">{session.title}</span>
          {pushed && (
            <span className="flex items-center gap-1 text-micro text-success font-medium">
              <CheckCircle2 size={12} />
              Pushed
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-small text-ink-faint">
          {session.project && (
            <span className="truncate max-w-[200px]">{session.project}</span>
          )}
          <span className="text-border-strong">·</span>
          <span className="truncate max-w-[320px]">{session.preview}</span>
        </div>
      </div>

      {/* Metadata */}
      <div className="flex items-center gap-3 shrink-0 text-small text-ink-faint font-mono">
        <span className="flex items-center gap-1">
          <MessageSquare size={12} />
          {session.messageCount}
        </span>
        {totalTokens !== undefined && (
          <span className="flex items-center gap-1">
            <Zap size={12} />
            {formatTokensFallback(totalTokens)}
          </span>
        )}
        <span>{relativeTimeFallback(session.startedAt)}</span>
      </div>
    </div>
  );
}
