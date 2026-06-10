import React, { createContext, useContext, useState, useCallback } from "react";
import { CheckCircle, XCircle, Info, AlertTriangle, X } from "lucide-react";
import { cn } from "./cn";

type ToastVariant = "success" | "error" | "info" | "warn";

interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  toast: (message: string, variant?: ToastVariant) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  warn: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}

const icons: Record<ToastVariant, React.ReactNode> = {
  success: <CheckCircle size={15} className="text-success shrink-0" />,
  error: <XCircle size={15} className="text-danger shrink-0" />,
  info: <Info size={15} className="text-accent shrink-0" />,
  warn: <AlertTriangle size={15} className="text-warn shrink-0" />,
};

const variantBorders: Record<ToastVariant, string> = {
  success: "border-[#b6dac7]",
  error: "border-[#f5c5bd]",
  info: "border-accent/30",
  warn: "border-[#f0d080]",
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, variant: ToastVariant = "info") => {
      const id = Math.random().toString(36).slice(2);
      setToasts((prev) => [...prev, { id, message, variant }]);
      setTimeout(() => dismiss(id), 4500);
    },
    [dismiss],
  );

  const success = useCallback((msg: string) => toast(msg, "success"), [toast]);
  const error = useCallback((msg: string) => toast(msg, "error"), [toast]);
  const info = useCallback((msg: string) => toast(msg, "info"), [toast]);
  const warn = useCallback((msg: string) => toast(msg, "warn"), [toast]);

  return (
    <ToastContext.Provider value={{ toast, success, error, info, warn }}>
      {children}
      {/* Toast container */}
      <div
        className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none"
        aria-live="polite"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto flex items-start gap-2.5 bg-bg-elevated border rounded-card px-3.5 py-3",
              "shadow-[0_2px_8px_rgba(26,24,21,0.12)] min-w-[260px] max-w-sm",
              "animate-in slide-in-from-bottom-2 fade-in duration-150",
              variantBorders[t.variant],
            )}
          >
            {icons[t.variant]}
            <p className="text-small text-ink flex-1">{t.message}</p>
            <button
              onClick={() => dismiss(t.id)}
              className="text-ink-faint hover:text-ink transition-colors duration-120 ml-1 mt-px"
            >
              <X size={13} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
