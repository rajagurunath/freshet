import React from "react";
import { cn } from "./cn";

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  hint?: string;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, error, hint, className, id, ...props }, ref) => {
    const textareaId = id ?? label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col gap-1.5 w-full">
        {label && (
          <label htmlFor={textareaId} className="text-small font-medium text-ink">
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={textareaId}
          className={cn(
            "w-full min-h-[80px] px-3 py-2 bg-bg-elevated border border-border rounded-[8px] text-body text-ink placeholder:text-ink-faint",
            "transition-colors duration-150 focus-ring resize-y",
            "hover:border-border-strong",
            error && "border-danger focus:ring-danger/40",
            className,
          )}
          {...props}
        />
        {error && <p className="text-small text-danger">{error}</p>}
        {hint && !error && <p className="text-small text-ink-faint">{hint}</p>}
      </div>
    );
  },
);

Textarea.displayName = "Textarea";
