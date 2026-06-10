import React from "react";
import { cn } from "./cn";

interface SkeletonProps {
  className?: string;
  lines?: number;
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "bg-bg-sunken rounded-[6px] animate-pulse",
        className,
      )}
    />
  );
}

export function SkeletonText({ lines = 3, className }: SkeletonProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn("h-4", i === lines - 1 && lines > 1 ? "w-3/5" : "w-full")}
        />
      ))}
    </div>
  );
}

export function SessionRowSkeleton() {
  return (
    <div className="flex items-center gap-4 px-4 py-3.5 border-b border-border last:border-0">
      <div className="flex-1 space-y-2">
        <div className="flex items-center gap-2">
          <Skeleton className="h-4 w-16 rounded-full" />
          <Skeleton className="h-4 w-48" />
        </div>
        <Skeleton className="h-3 w-72" />
      </div>
      <Skeleton className="h-3 w-16" />
    </div>
  );
}
