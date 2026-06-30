import React, { useEffect, useRef, useState } from "react";
import { Loader2, Check } from "lucide-react";
import { useSettings } from "@/store/settings";
import { makeApiClient, type GraphBuildProgress } from "@/lib/api/client";

/**
 * Global, bottom-of-app progress bar for the offline graph build. On first load
 * it kicks off building per-session graphs for every local session (idempotent —
 * already-built sessions are skipped) and shows live done/total progress. Hides
 * itself shortly after completion.
 */
export function GraphBuildBar() {
  const settings = useSettings();
  const [prog, setProg] = useState<GraphBuildProgress | null>(null);
  const [justFinished, setJustFinished] = useState(false);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!settings.apiBaseUrl) return;
    const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
    let active = true;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    // Kick off the build once on mount (resumable + cheap when already complete).
    client.buildAllGraphs().catch(() => {});

    const poll = async () => {
      try {
        const p = await client.getGraphBuildProgress();
        if (!active) return;
        setProg(p);
        if (!p.running && p.total > 0 && p.done >= p.total) {
          setJustFinished(true);
          if (hideTimer.current) clearTimeout(hideTimer.current);
          hideTimer.current = setTimeout(() => active && setProg(null), 2500);
          return; // stop polling
        }
      } catch {
        /* hub unreachable — leave the bar hidden */
      }
      pollTimer = setTimeout(poll, 1000);
    };
    poll();

    return () => {
      active = false;
      if (pollTimer) clearTimeout(pollTimer);
      if (hideTimer.current) clearTimeout(hideTimer.current);
    };
  }, [settings.apiBaseUrl, settings.apiKey]);

  if (!prog || prog.total === 0) return null;
  const pct = prog.total ? Math.round((prog.done / prog.total) * 100) : 0;
  const done = !prog.running && prog.done >= prog.total;

  return (
    <div className="shrink-0 border-t border-border bg-bg-elevated px-5 py-2 flex items-center gap-3">
      {done ? (
        <Check size={14} className="text-accent shrink-0" />
      ) : (
        <Loader2 size={14} className="text-accent shrink-0 animate-spin" />
      )}
      <span className="text-small text-ink-soft whitespace-nowrap">
        {done ? "Knowledge graphs ready" : "Building knowledge graphs…"}
      </span>
      <div className="flex-1 h-1.5 rounded-full bg-bg-sunken overflow-hidden">
        <div
          className="h-full bg-accent transition-[width] duration-300 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-micro text-ink-faint font-mono whitespace-nowrap">
        {prog.done}/{prog.total}
      </span>
    </div>
  );
}
