import React from "react";
import { CheckCircle2, Clock, DollarSign, MessageSquare, Scissors, XCircle, Zap } from "lucide-react";
import { cn } from "./ui/cn";
import { ToolChip } from "./ToolChip";
import { relativeTime, formatTokens } from "@/lib/format";
import { estimateCost, formatCost } from "@/lib/pricing";
import type { NormalizedSession } from "@/lib/types";

interface SessionRowProps {
  session: NormalizedSession;
  pushed?: boolean;
  reviewStatus?: "pending" | "rejected";
  onClick?: () => void;
  className?: string;
}

export function SessionRow({ session, pushed = false, reviewStatus, onClick, className }: SessionRowProps) {
  const totalTokens =
    session.tokens ? session.tokens.input + session.tokens.output : undefined;

  const costEstimate = session.tokens ? estimateCost(session) : null;

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
          {session.compacted && (
            <span
              className="flex items-center gap-1 text-micro text-amber-600 font-medium px-1.5 py-0.5 rounded-full bg-amber-50 border border-amber-200"
              title="This session was compacted with /compact"
            >
              <Scissors size={11} />
              compacted
            </span>
          )}
          {reviewStatus === "pending" ? (
            <span
              className="flex items-center gap-1 text-micro text-warn font-medium px-1.5 py-0.5 rounded-full bg-[#fef9e7] border border-[#f0d080]"
              title="Held in the review queue — awaiting approval before it joins the company brain"
            >
              <Clock size={11} />
              In review
            </span>
          ) : reviewStatus === "rejected" ? (
            <span
              className="flex items-center gap-1 text-micro text-danger font-medium px-1.5 py-0.5 rounded-full bg-[#fdecea] border border-[#f5c5bd]"
              title="Rejected in review — this session was not indexed"
            >
              <XCircle size={11} />
              Rejected
            </span>
          ) : pushed ? (
            <span className="flex items-center gap-1 text-micro text-success font-medium">
              <CheckCircle2 size={12} />
              Pushed
            </span>
          ) : null}
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
            {formatTokens(totalTokens)}
          </span>
        )}
        {costEstimate !== null && costEstimate.usd > 0 && (
          <span className="flex items-center gap-1" title={costEstimate.known ? "Estimated cost" : "Estimated cost (unknown model — approximate)"}>
            <DollarSign size={12} />
            {formatCost(costEstimate).replace("$", "")}
            {!costEstimate.known && <span className="text-micro">~</span>}
          </span>
        )}
        <span>{relativeTime(session.startedAt)}</span>
      </div>
    </div>
  );
}
