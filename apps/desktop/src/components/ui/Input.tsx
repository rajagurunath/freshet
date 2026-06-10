import React from "react";
import { cn } from "./cn";

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  hint?: string;
  leading?: React.ReactNode;
  trailing?: React.ReactNode;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, hint, leading, trailing, className, id, ...props }, ref) => {
    const inputId = id ?? label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col gap-1.5 w-full">
        {label && (
          <label htmlFor={inputId} className="text-small font-medium text-ink">
            {label}
          </label>
        )}
        <div className="relative flex items-center">
          {leading && (
            <span className="absolute left-3 text-ink-faint pointer-events-none flex items-center">
              {leading}
            </span>
          )}
          <input
            ref={ref}
            id={inputId}
            className={cn(
              "w-full h-9 px-3 bg-bg-elevated border border-border rounded-[8px] text-body text-ink placeholder:text-ink-faint",
              "transition-colors duration-150 focus-ring",
              "hover:border-border-strong",
              error && "border-danger focus:ring-danger/40",
              leading && "pl-9",
              trailing && "pr-9",
              className,
            )}
            {...props}
          />
          {trailing && (
            <span className="absolute right-3 text-ink-faint pointer-events-none flex items-center">
              {trailing}
            </span>
          )}
        </div>
        {error && <p className="text-small text-danger">{error}</p>}
        {hint && !error && <p className="text-small text-ink-faint">{hint}</p>}
      </div>
    );
  },
);

Input.displayName = "Input";
