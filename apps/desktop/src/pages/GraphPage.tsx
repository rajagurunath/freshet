import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Share2, Search, RefreshCw, X, ExternalLink, Cpu, Link2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { cn } from "@/components/ui/cn";
import { useApp } from "@/store/app";
import { useSettings } from "@/store/settings";
import { makeApiClient } from "@/lib/api/client";
import {
  computeForceLayout,
  kindColor,
  nodeRadius,
  GRAPH_KIND_COLORS,
  type GraphData,
  type GraphNode,
} from "@/lib/graph";

const DEPTH_OPTIONS = [
  { value: "1", label: "1 hop" },
  { value: "2", label: "2 hops" },
  { value: "3", label: "3 hops" },
];

const LEGEND_KINDS = ["repo", "service", "feature", "person", "decision", "tool", "pr"];

export function GraphPage() {
  const navigate = useNavigate();
  const settings = useSettings();
  const localSessions = useApp((s) => s.sessions);
  const { info: toastInfo, error: toastError } = useToast();
  const [backfilling, setBackfilling] = useState(false);
  const [resolving, setResolving] = useState(false);

  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [focusInput, setFocusInput] = useState("");
  const [focus, setFocus] = useState("");
  const [depth, setDepth] = useState("1");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Measure the canvas so the layout fills the available space.
  const canvasRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 620 });
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setSize({ width: Math.round(rect.width), height: Math.round(rect.height) });
      }
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const load = useCallback(async () => {
    if (!settings.apiBaseUrl) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
      const graph = await client.getGraph(
        focus ? { focus, depth: Number(depth) } : undefined,
      );
      setData(graph);
      setSelectedId(null);
    } catch {
      setError("Could not load the knowledge graph. Check your connection settings.");
      toastError("Failed to load the knowledge graph.");
    } finally {
      setLoading(false);
    }
  }, [settings.apiBaseUrl, settings.apiKey, focus, depth, toastError]);

  useEffect(() => {
    load();
  }, [load]);

  const handleBackfill = useCallback(async () => {
    if (!settings.apiBaseUrl) return;
    setBackfilling(true);
    try {
      const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
      const result = await client.backfillGraph();
      toastInfo(`Queued ${result.enqueued} session${result.enqueued !== 1 ? "s" : ""} for extraction`);
      // Re-fetch the graph after a short delay to pick up any fast extractions
      setTimeout(() => void load(), 5000);
    } catch {
      toastError("Failed to queue graph extraction.");
    } finally {
      setBackfilling(false);
    }
  }, [settings.apiBaseUrl, settings.apiKey, toastInfo, toastError, load]);

  const handleResolve = useCallback(async () => {
    if (!settings.apiBaseUrl) return;
    setResolving(true);
    try {
      const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
      const result = await client.resolveBackfillGraph();
      toastInfo(
        `Linking ${result.enqueued} session${result.enqueued !== 1 ? "s" : ""} — same-concept nodes will connect`,
      );
      // Re-fetch after a short delay to pick up any same_as edges just written.
      setTimeout(() => void load(), 5000);
    } catch {
      toastError("Failed to queue cross-session linking.");
    } finally {
      setResolving(false);
    }
  }, [settings.apiBaseUrl, settings.apiKey, toastInfo, toastError, load]);

  const layout = useMemo(() => {
    if (!data) return new Map<string, { x: number; y: number }>();
    return computeForceLayout(data.nodes, data.edges, {
      width: size.width,
      height: size.height,
    });
  }, [data, size.width, size.height]);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of data?.nodes ?? []) m.set(n.id, n);
    return m;
  }, [data]);

  const selected = selectedId ? nodeById.get(selectedId) ?? null : null;

  /** Edges touching the selected node (for the side panel + highlight). */
  const selectedEdges = useMemo(() => {
    if (!selected || !data) return [];
    return data.edges.filter((e) => e.src === selected.id || e.dst === selected.id);
  }, [selected, data]);

  const neighborIds = useMemo(() => {
    const ids = new Set<string>();
    for (const e of selectedEdges) {
      ids.add(e.src);
      ids.add(e.dst);
    }
    return ids;
  }, [selectedEdges]);

  const localSessionIds = useMemo(
    () => new Set(localSessions.map((s) => s.id)),
    [localSessions],
  );

  const hasApi = Boolean(settings.apiBaseUrl);
  const isEmpty = !loading && !error && (data?.nodes.length ?? 0) === 0;

  const submitFocus = (e: React.FormEvent) => {
    e.preventDefault();
    setFocus(focusInput.trim());
  };

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center justify-between gap-4 px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <div className="shrink-0">
          <h1 className="text-h2 font-semibold text-ink">Knowledge Graph</h1>
          <p className="text-small text-ink-faint mt-0.5">
            Repos, services, features and decisions linked across sessions
          </p>
        </div>
        <form onSubmit={submitFocus} className="flex items-center gap-2 max-w-[480px] w-full">
          <Input
            placeholder="Focus on a node (e.g. checkout-service)…"
            value={focusInput}
            onChange={(e) => setFocusInput(e.target.value)}
            leading={<Search size={14} />}
            aria-label="Focus node"
          />
          <div className="w-[110px] shrink-0">
            <Select
              options={DEPTH_OPTIONS}
              value={depth}
              onChange={(e) => setDepth(e.target.value)}
              aria-label="Neighborhood depth"
            />
          </div>
          <button
            type="button"
            onClick={() => {
              setFocusInput("");
              setFocus("");
            }}
            className={cn(
              "h-9 px-3 rounded-[8px] border border-border text-small text-ink-soft",
              "hover:bg-bg-sunken hover:text-ink transition-colors duration-120 shrink-0",
              !focus && "opacity-40 pointer-events-none",
            )}
          >
            Clear
          </button>
          <button
            type="button"
            onClick={load}
            aria-label="Refresh graph"
            className="h-9 w-9 flex items-center justify-center rounded-[8px] border border-border text-ink-soft hover:bg-bg-sunken hover:text-ink transition-colors duration-120 shrink-0"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          </button>
          {hasApi && (
            <Button
              variant="secondary"
              size="sm"
              loading={backfilling}
              onClick={() => void handleBackfill()}
            >
              <Cpu size={13} />
              Generate graph
            </Button>
          )}
          {hasApi && (
            <Button
              variant="secondary"
              size="sm"
              loading={resolving}
              onClick={() => void handleResolve()}
              title="Link same-concept nodes across sessions (creates same_as edges)"
            >
              <Link2 size={13} />
              Link concepts
            </Button>
          )}
        </form>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 flex">
        {/* Canvas */}
        <div ref={canvasRef} className="flex-1 min-w-0 relative bg-bg">
          {!hasApi ? (
            <EmptyState
              icon={<Share2 size={40} strokeWidth={1.25} />}
              headline="Connect a hub first"
              body="Add your Context Hub API URL and key in Settings — the hub extracts the knowledge graph from pushed sessions."
              cta={
                <button
                  className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                  onClick={() => navigate("/settings")}
                >
                  Go to Settings
                </button>
              }
            />
          ) : loading ? (
            <div className="absolute inset-6">
              <Skeleton className="w-full h-full rounded-card" />
            </div>
          ) : error ? (
            <EmptyState
              icon={<Share2 size={40} strokeWidth={1.25} />}
              headline="Could not load the graph"
              body={error}
              cta={
                <button
                  className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                  onClick={load}
                >
                  Retry
                </button>
              }
            />
          ) : isEmpty ? (
            <EmptyState
              icon={<Share2 size={40} strokeWidth={1.25} />}
              headline={focus ? "No nodes match that focus" : "No graph yet"}
              body={
                focus
                  ? "Try a different node name, or clear the focus to see the full graph."
                  : "Push sessions to the hub — graph extraction runs automatically and entities will appear here."
              }
              cta={
                !focus ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={backfilling}
                    onClick={() => void handleBackfill()}
                  >
                    <Cpu size={13} />
                    Generate graph
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <svg
              width="100%"
              height="100%"
              viewBox={`0 0 ${size.width} ${size.height}`}
              role="img"
              aria-label="Knowledge graph"
              onClick={() => setSelectedId(null)}
            >
              {/* Edges */}
              {data!.edges.map((e) => {
                const a = layout.get(e.src);
                const b = layout.get(e.dst);
                if (!a || !b) return null;
                const active =
                  selected && (e.src === selected.id || e.dst === selected.id);
                return (
                  <g key={e.id}>
                    <line
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke={active ? "#F2541B" : "#D8D2C4"}
                      strokeWidth={active ? 1.5 : 1}
                      strokeOpacity={selected && !active ? 0.25 : 0.8}
                    >
                      <title>{e.rel.replace(/_/g, " ")}</title>
                    </line>
                  </g>
                );
              })}
              {/* Nodes */}
              {data!.nodes.map((n) => {
                const p = layout.get(n.id);
                if (!p) return null;
                const c = kindColor(n.kind);
                const r = nodeRadius(n);
                const dimmed = selected && n.id !== selected.id && !neighborIds.has(n.id);
                return (
                  <g
                    key={n.id}
                    transform={`translate(${p.x}, ${p.y})`}
                    className="cursor-pointer"
                    opacity={dimmed ? 0.3 : 1}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      setSelectedId(n.id);
                    }}
                  >
                    <circle
                      r={r}
                      fill={c.fill}
                      stroke={selectedId === n.id ? "#F2541B" : c.stroke}
                      strokeWidth={selectedId === n.id ? 2 : 1.25}
                    />
                    <text
                      y={r + 13}
                      textAnchor="middle"
                      fontSize={11}
                      fill="#6b6657"
                      style={{ pointerEvents: "none", userSelect: "none" }}
                    >
                      {n.name.length > 24 ? `${n.name.slice(0, 23)}…` : n.name}
                    </text>
                    <title>{`${c.label}: ${n.name}`}</title>
                  </g>
                );
              })}
            </svg>
          )}

          {/* Legend */}
          {hasApi && !loading && !error && !isEmpty && (
            <div className="absolute bottom-4 left-4 flex flex-wrap gap-x-3 gap-y-1 px-3 py-2 rounded-[8px] border border-border bg-bg-elevated/90">
              {LEGEND_KINDS.map((kind) => {
                const c = GRAPH_KIND_COLORS[kind];
                return (
                  <span key={kind} className="flex items-center gap-1.5 text-micro text-ink-faint">
                    <span
                      className="w-2.5 h-2.5 rounded-full border"
                      style={{ backgroundColor: c.fill, borderColor: c.stroke }}
                    />
                    {c.label}
                  </span>
                );
              })}
            </div>
          )}
        </div>

        {/* Side panel */}
        {selected && (
          <aside className="w-[320px] shrink-0 border-l border-border bg-bg-elevated overflow-y-auto">
            <div className="flex items-start justify-between gap-2 px-4 py-4 border-b border-border">
              <div className="min-w-0">
                <Badge
                  className="mb-1.5"
                  color="default"
                >
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: kindColor(selected.kind).ink }}
                  />
                  {kindColor(selected.kind).label}
                </Badge>
                <h2 className="text-h3 font-semibold text-ink break-words">{selected.name}</h2>
              </div>
              <button
                onClick={() => setSelectedId(null)}
                aria-label="Close panel"
                className="p-1 rounded-[6px] text-ink-faint hover:text-ink hover:bg-bg-sunken transition-colors duration-120 shrink-0"
              >
                <X size={14} />
              </button>
            </div>

            <div className="px-4 py-4 space-y-5">
              {selected.summary && (
                <p className="text-small text-ink-soft leading-relaxed">{selected.summary}</p>
              )}

              {/* Relations */}
              {selectedEdges.length > 0 && (
                <div>
                  <h3 className="text-micro font-semibold uppercase tracking-wide text-ink-faint mb-2">
                    Relations
                  </h3>
                  <ul className="space-y-1.5">
                    {selectedEdges.map((e) => {
                      const otherId = e.src === selected.id ? e.dst : e.src;
                      const other = nodeById.get(otherId);
                      if (!other) return null;
                      return (
                        <li key={e.id}>
                          <button
                            onClick={() => setSelectedId(other.id)}
                            className="w-full flex items-center gap-2 text-left text-small text-ink-soft hover:text-ink transition-colors duration-120"
                          >
                            <span
                              className="w-2 h-2 rounded-full shrink-0 border"
                              style={{
                                backgroundColor: kindColor(other.kind).fill,
                                borderColor: kindColor(other.kind).stroke,
                              }}
                            />
                            <span className="truncate">{other.name}</span>
                            <span className="ml-auto text-micro text-ink-faint shrink-0">
                              {e.rel.replace(/_/g, " ")}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}

              {/* Linked sessions */}
              <div>
                <h3 className="text-micro font-semibold uppercase tracking-wide text-ink-faint mb-2">
                  Linked sessions ({selected.sessionIds.length})
                </h3>
                {selected.sessionIds.length === 0 ? (
                  <p className="text-small text-ink-faint">No session provenance recorded.</p>
                ) : (
                  <ul className="space-y-1">
                    {selected.sessionIds.map((sid) => {
                      const isLocal = localSessionIds.has(sid);
                      return (
                        <li key={sid}>
                          {isLocal ? (
                            <button
                              onClick={() => navigate(`/sessions/${sid}`)}
                              className="flex items-center gap-1.5 text-small font-mono text-accent hover:text-accent-ink transition-colors duration-120 max-w-full"
                            >
                              <span className="truncate">{sid}</span>
                              <ExternalLink size={11} className="shrink-0" />
                            </button>
                          ) : (
                            <span className="block text-small font-mono text-ink-faint truncate">
                              {sid}
                            </span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
