import React from "react";
import { cn } from "./cn";

type BadgeColor =
  | "default"
  | "accent"
  | "success"
  | "warn"
  | "danger"
  | "slate"
  | "teal"
  | "orange";

interface BadgeProps {
  children: React.ReactNode;
  color?: BadgeColor;
  className?: string;
}

const colorClasses: Record<BadgeColor, string> = {
  default: "bg-bg-sunken text-ink-soft border-border",
  accent: "bg-accent-wash text-accent-ink border-accent/30",
  success: "bg-[#e8f5ef] text-[#2e7d52] border-[#b6dac7]",
  warn: "bg-[#fef9e7] text-warn border-[#f0d080]",
  danger: "bg-[#fdecea] text-danger border-[#f5c5bd]",
  slate: "bg-[#eef0f4] text-[#3a4460] border-[#c8cdd8]",
  teal: "bg-[#e6f4f4] text-[#1a6b6b] border-[#a8d4d4]",
  orange: "bg-accent-wash text-accent-ink border-accent/30",
};

export function Badge({ children, color = "default", className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-micro font-medium border",
        colorClasses[color],
        className,
      )}
    >
      {children}
    </span>
  );
}
