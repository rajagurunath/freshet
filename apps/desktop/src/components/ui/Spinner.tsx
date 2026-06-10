import React from "react";
import { cn } from "./cn";

interface SpinnerProps {
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function Spinner({ size = "md", className }: SpinnerProps) {
  const sizeClasses = {
    sm: "h-3 w-3 border-[1.5px]",
    md: "h-4 w-4 border-2",
    lg: "h-6 w-6 border-2",
  };

  return (
    <span
      className={cn(
        "inline-block rounded-full border-current border-t-transparent animate-spin",
        sizeClasses[size],
        className,
      )}
      aria-label="Loading"
    />
  );
}
