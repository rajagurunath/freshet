import React from "react";
import { cn } from "./cn";

export interface TabItem<T extends string = string> {
  value: T;
  label: string;
  count?: number;
}

interface TabsProps<T extends string = string> {
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}

export function Tabs<T extends string = string>({
  items,
  value,
  onChange,
  className,
}: TabsProps<T>) {
  return (
    <div
      className={cn("flex gap-0.5 bg-bg-sunken p-0.5 rounded-[8px]", className)}
      role="tablist"
    >
      {items.map((item) => (
        <button
          key={item.value}
          role="tab"
          aria-selected={value === item.value}
          onClick={() => onChange(item.value)}
          className={cn(
            "flex items-center gap-1.5 h-7 px-3 rounded-[6px] text-small font-medium transition-all duration-120 ease-out",
            value === item.value
              ? "bg-bg-elevated text-ink shadow-none border border-border"
              : "text-ink-soft hover:text-ink",
          )}
        >
          {item.label}
          {item.count !== undefined && (
            <span
              className={cn(
                "text-micro font-mono px-1.5 py-0.5 rounded-full",
                value === item.value
                  ? "bg-accent-wash text-accent-ink"
                  : "bg-border text-ink-faint",
              )}
            >
              {item.count}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
