import React from "react";
import { cn } from "./cn";

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  description?: string;
  disabled?: boolean;
  className?: string;
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled = false,
  className,
}: ToggleProps) {
  return (
    <label
      className={cn(
        "flex items-start gap-3 cursor-pointer select-none",
        disabled && "opacity-50 pointer-events-none",
        className,
      )}
    >
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex shrink-0 h-5 w-9 rounded-full border-2 transition-colors duration-150 ease-out focus-ring",
          checked
            ? "bg-accent border-accent"
            : "bg-bg-sunken border-border-strong",
        )}
      >
        <span
          className={cn(
            "inline-block h-3.5 w-3.5 mt-px rounded-full bg-white shadow-sm transition-transform duration-150 ease-out",
            checked ? "translate-x-4" : "translate-x-0",
          )}
        />
      </button>
      {(label || description) && (
        <span className="flex flex-col">
          {label && <span className="text-small font-medium text-ink">{label}</span>}
          {description && <span className="text-small text-ink-faint">{description}</span>}
        </span>
      )}
    </label>
  );
}
