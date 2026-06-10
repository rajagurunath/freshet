import React from "react";
import { cn } from "./cn";

interface EmptyStateProps {
  icon: React.ReactNode;
  headline: string;
  body: string;
  cta?: React.ReactNode;
  className?: string;
}

export function EmptyState({ icon, headline, body, cta, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center py-16 px-8 gap-4",
        className,
      )}
    >
      <div className="text-ink-faint">{icon}</div>
      <div className="space-y-1.5">
        <h3 className="text-h3 font-semibold text-ink">{headline}</h3>
        <p className="text-body text-ink-soft max-w-[360px]">{body}</p>
      </div>
      {cta && <div className="mt-2">{cta}</div>}
    </div>
  );
}
