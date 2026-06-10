import React from "react";
import { cn } from "./ui/cn";
import { Card } from "./ui/Card";

interface StatTileProps {
  label: string;
  value: string | number;
  icon?: React.ReactNode;
  trend?: string;
  className?: string;
}

export function StatTile({ label, value, icon, trend, className }: StatTileProps) {
  return (
    <Card className={cn("p-5 flex flex-col gap-2", className)}>
      <div className="flex items-center justify-between">
        <span className="text-small text-ink-soft font-medium">{label}</span>
        {icon && <span className="text-ink-faint">{icon}</span>}
      </div>
      <div className="flex items-end gap-2">
        <span className="text-h2 font-semibold text-ink font-mono">{value}</span>
        {trend && (
          <span className="text-small text-ink-faint mb-0.5">{trend}</span>
        )}
      </div>
    </Card>
  );
}
