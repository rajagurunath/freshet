import React from "react";
import { cn } from "./ui/cn";

export interface BarItem {
  label: React.ReactNode;
  value: number;
  /** Optional value display override (e.g. "12.4k"). */
  display?: string;
  /** Tailwind bg class for the bar fill. */
  barClass?: string;
}

interface BarListProps {
  items: BarItem[];
  className?: string;
  emptyText?: string;
}

/** A compact horizontal bar breakdown (label · proportional bar · value). */
export function BarList({ items, className, emptyText = "No data yet" }: BarListProps) {
  const max = Math.max(1, ...items.map((i) => i.value));
  if (items.length === 0) {
    return <p className="text-small text-ink-faint">{emptyText}</p>;
  }
  return (
    <div className={cn("flex flex-col gap-2.5", className)}>
      {items.map((item, idx) => (
        <div key={idx} className="flex items-center gap-3">
          <div className="w-32 shrink-0 text-small text-ink-soft truncate">{item.label}</div>
          <div className="flex-1 h-2 rounded-full bg-bg-sunken overflow-hidden">
            <div
              className={cn("h-full rounded-full", item.barClass ?? "bg-accent")}
              style={{ width: `${Math.max(4, (item.value / max) * 100)}%` }}
            />
          </div>
          <div className="w-12 shrink-0 text-right text-small font-mono text-ink">
            {item.display ?? item.value}
          </div>
        </div>
      ))}
    </div>
  );
}
