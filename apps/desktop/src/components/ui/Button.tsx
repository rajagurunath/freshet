import React from "react";
import { cn } from "./cn";
import { Spinner } from "./Spinner";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  children: React.ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-2 font-sans font-medium rounded-[8px] transition-all duration-150 ease-out select-none cursor-pointer disabled:opacity-50 disabled:pointer-events-none focus-ring";

  const variants: Record<ButtonVariant, string> = {
    primary:
      "bg-accent text-white hover:bg-accent-ink active:scale-[0.98] border border-accent hover:border-accent-ink",
    secondary:
      "bg-bg-elevated text-ink border border-border hover:border-border-strong hover:bg-bg-sunken active:scale-[0.98]",
    ghost:
      "bg-transparent text-ink-soft border border-transparent hover:bg-bg-sunken hover:text-ink active:scale-[0.98]",
    danger:
      "bg-danger text-white border border-danger hover:opacity-90 active:scale-[0.98]",
  };

  const sizes: Record<ButtonSize, string> = {
    sm: "h-7 px-3 text-small",
    md: "h-9 px-4 text-body",
  };

  return (
    <button
      className={cn(base, variants[variant], sizes[size], className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Spinner size="sm" />}
      {children}
    </button>
  );
}
