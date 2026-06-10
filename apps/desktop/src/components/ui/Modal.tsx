import React, { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { cn } from "./cn";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  description?: string;
  children: React.ReactNode;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  size = "md",
  className,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  // Trap focus within modal
  useEffect(() => {
    if (!open || !dialogRef.current) return;
    const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (focusable.length > 0) focusable[0].focus();
  }, [open]);

  if (!open) return null;

  const sizeClasses = {
    sm: "max-w-sm",
    md: "max-w-lg",
    lg: "max-w-2xl",
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? "modal-title" : undefined}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-ink/30 backdrop-blur-[2px]"
        onClick={onClose}
      />

      {/* Panel */}
      <div
        ref={dialogRef}
        className={cn(
          "relative w-full bg-bg-elevated border border-border rounded-card shadow-lg",
          "animate-in fade-in zoom-in-95 duration-150",
          sizeClasses[size],
          className,
        )}
      >
        {(title || description) && (
          <div className="flex items-start justify-between p-6 pb-0">
            <div>
              {title && (
                <h2 id="modal-title" className="text-h3 font-semibold text-ink">
                  {title}
                </h2>
              )}
              {description && (
                <p className="mt-1 text-small text-ink-soft">{description}</p>
              )}
            </div>
            <button
              onClick={onClose}
              className="ml-4 text-ink-faint hover:text-ink transition-colors duration-120 focus-ring rounded-[4px]"
            >
              <X size={16} />
            </button>
          </div>
        )}
        <div className="p-6">{children}</div>
      </div>
    </div>
  );
}
