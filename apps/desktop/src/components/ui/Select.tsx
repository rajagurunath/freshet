import React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "./cn";

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  hint?: string;
  options: SelectOption[];
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, error, hint, options, className, id, ...props }, ref) => {
    const selectId = id ?? label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="flex flex-col gap-1.5 w-full">
        {label && (
          <label htmlFor={selectId} className="text-small font-medium text-ink">
            {label}
          </label>
        )}
        <div className="relative">
          <select
            ref={ref}
            id={selectId}
            className={cn(
              "w-full h-9 pl-3 pr-8 bg-bg-elevated border border-border rounded-[8px] text-body text-ink",
              "transition-colors duration-150 focus-ring appearance-none cursor-pointer",
              "hover:border-border-strong",
              error && "border-danger",
              className,
            )}
            {...props}
          >
            {options.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          <ChevronDown
            size={14}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none"
          />
        </div>
        {error && <p className="text-small text-danger">{error}</p>}
        {hint && !error && <p className="text-small text-ink-faint">{hint}</p>}
      </div>
    );
  },
);

Select.displayName = "Select";
