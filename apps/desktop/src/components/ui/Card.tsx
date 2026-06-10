import React from "react";
import { cn } from "./cn";

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  hoverable?: boolean;
  children: React.ReactNode;
}

export function Card({ hoverable = false, className, children, ...props }: CardProps) {
  return (
    <div
      className={cn(
        "bg-bg-elevated border border-border rounded-card",
        hoverable &&
          "cursor-pointer transition-colors duration-150 hover:bg-bg-sunken hover:border-border-strong",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
