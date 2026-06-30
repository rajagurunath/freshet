import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Share2, Search, RefreshCw, X, ExternalLink, Cpu, Link2, SlidersHorizontal } from "lucide-react";
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
import { loadRejected, saveRejected, entityKey } from "@/lib/entityReview";

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

  // Pan/zoom viewport for the graph (translate x/y + scale k).
  const [reviewOpen, setReviewOpen] = useState(false);
  const [rejected, setRejected] = useState<Set<string>>(() => loadRejected());
  const toggleRejected = useCallback((kind: string, name: string) => {
    setRejected((prev) => {
      const next = new Set(prev);
      const key = entityKey(kind, name);
      next.has(key) ? next.delete(key) : next.add(key);
      saveRejected(next);
      return next;
    });
  }, []);

  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const panRef = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const [panning, setPanning] = useState(false);

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

  // Drop user-rejected entities (and their edges) from everything below.
  const visibleNodes = useMemo(
    () => (data?.nodes ?? []).filter((n) => !rejected.has(entityKey(n.kind, n.name))),
    [data, rejected],
  );
  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => (data?.edges ?? []).filter((e) => visibleNodeIds.has(e.src) && visibleNodeIds.has(e.dst)),
    [data, visibleNodeIds],
  );

  // Lay out in a virtual canvas that grows with node count, so nodes have room
  // to spread instead of overlapping in the small viewport. We then auto-fit it.
  const layoutDims = useMemo(() => {
    const nn = visibleNodes.length || 1;
    const span = Math.ceil(Math.sqrt(nn));
    return {
      width: Math.max(size.width, span * 260),
      height: Math.max(size.height, span * 195),
    };
  }, [visibleNodes, size.width, size.height]);

  const layout = useMemo(() => {
    if (!visibleNodes.length) return new Map<string, { x: number; y: number }>();
    return computeForceLayout(visibleNodes, visibleEdges, layoutDims);
  }, [visibleNodes, visibleEdges, layoutDims]);

  // Auto-fit the (possibly larger) layout into the viewport whenever it changes,
  // so the whole graph is visible; the user can then wheel-zoom / drag to explore.
  useEffect(() => {
    if (layout.size === 0 || !size.width) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of layout.values()) {
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, p.y);
    }
    const w = maxX - minX || 1;
    const h = maxY - minY || 1;
    const pad = 80;
    const k = Math.min((size.width - pad) / w, (size.height - pad) / h, 1.4);
    setView({
      k,
      x: (size.width - w * k) / 2 - minX * k,
      y: (size.height - h * k) / 2 - minY * k,
    });
  }, [layout, size.width, size.height]);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of visibleNodes) m.set(n.id, n);
    return m;
  }, [visibleNodes]);

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

  // Live keyword search: nodes whose name matches the box are highlighted and
  // everything else dims — a client-side "find in this graph" (no refetch).
  const matchIds = useMemo(() => {
    const q = focusInput.trim().toLowerCase();
    if (!q) return null;
    const ids = new Set<string>();
    for (const n of visibleNodes) {
      if (n.name.toLowerCase().includes(q) || n.kind.toLowerCase().includes(q)) {
        ids.add(n.id);
      }
    }
    return ids;
  }, [focusInput, visibleNodes]);

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
            placeholder="Search nodes… (Enter = focus to neighbourhood)"
            value={focusInput}
            onChange={(e) => setFocusInput(e.target.value)}
            leading={<Search size={14} />}
            aria-label="Search nodes"
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
          {hasApi && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setReviewOpen(true)}
              title="Review extracted entities — hide ones that aren't real"
            >
              <SlidersHorizontal size={13} />
              Review
            </Button>
          )}
        </form>
      </div>

      {/* Entity review modal — human-in-the-loop curation of NER entities. */}
      {reviewOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6"
          onClick={() => setReviewOpen(false)}
        >
          <div
            className="w-[560px] max-h-[80vh] flex flex-col rounded-card border border-border bg-bg-elevated shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border">
              <div>
                <h2 className="text-h3 font-semibold text-ink">Review entities</h2>
                <p className="text-small text-ink-faint mt-0.5">
                  Uncheck anything that isn't a real entity — it's hidden from the graph
                  and remembered for next time. {rejected.size} hidden.
                </p>
              </div>
              <button
                onClick={() => setReviewOpen(false)}
                className="text-ink-faint hover:text-ink"
                aria-label="Close"
              >
                <X size={18} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-3">
              {LEGEND_KINDS.map((kind) => {
                const items = (data?.nodes ?? [])
                  .filter((n) => n.kind === kind)
                  .sort((a, b) => a.name.localeCompare(b.name));
                if (!items.length) return null;
                const c = GRAPH_KIND_COLORS[kind];
                return (
                  <div key={kind} className="mb-3">
                    <div className="text-micro uppercase tracking-wide text-ink-faint mb-1">
                      {c.label} ({items.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {items.map((n) => {
                        const isRejected = rejected.has(entityKey(n.kind, n.name));
                        return (
                          <button
                            key={n.id}
                            onClick={() => toggleRejected(n.kind, n.name)}
                            className={cn(
                              "px-2 py-1 rounded-[6px] border text-small transition-colors duration-120",
                              isRejected
                                ? "border-border bg-bg-sunken text-ink-faint line-through opacity-60"
                                : "border-border bg-bg text-ink-soft hover:border-accent",
                            )}
                            title={isRejected ? "Hidden — click to restore" : "Click to hide"}
                          >
                            {n.name}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="flex items-center justify-between gap-3 px-5 py-3 border-t border-border">
              <button
                onClick={() => {
                  setRejected(new Set());
                  saveRejected(new Set());
                }}
                className="text-small text-ink-soft hover:text-ink"
              >
                Restore all
              </button>
              <Button variant="primary" size="sm" onClick={() => setReviewOpen(false)}>
                Done
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Body */}
      <div className="flex-1 min-h-0 flex">
        {/* Canvas */}
        <div ref={canvasRef} className="flex-1 min-w-0 relative bg-bg">
          {!hasApi ? (
            <EmptyState
              icon={<Share2 size={40} strokeWidth={1.25} />}
              headline="Connect a hub first"
              body="Add your Freshet API URL and key in Settings — the hub extracts the knowledge graph from pushed sessions."
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
              style={{ cursor: panning ? "grabbing" : "grab", touchAction: "none" }}
              onClick={() => setSelectedId(null)}
              onWheel={(ev) => {
                ev.preventDefault();
                const rect = ev.currentTarget.getBoundingClientRect();
                const mx = ev.clientX - rect.left;
                const my = ev.clientY - rect.top;
                setView((v) => {
                  const factor = Math.exp(-ev.deltaY * 0.0015);
                  const k = Math.min(4, Math.max(0.2, v.k * factor));
                  // keep the point under the cursor fixed while zooming
                  const x = mx - ((mx - v.x) * k) / v.k;
                  const y = my - ((my - v.y) * k) / v.k;
                  return { x, y, k };
                });
              }}
              onMouseDown={(ev) => {
                panRef.current = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y };
                setPanning(true);
              }}
              onMouseMove={(ev) => {
                if (!panRef.current) return;
                const dx = ev.clientX - panRef.current.x;
                const dy = ev.clientY - panRef.current.y;
                setView((v) => ({ ...v, x: panRef.current!.vx + dx, y: panRef.current!.vy + dy }));
              }}
              onMouseUp={() => {
                panRef.current = null;
                setPanning(false);
              }}
              onMouseLeave={() => {
                panRef.current = null;
                setPanning(false);
              }}
              onDoubleClick={() => setView({ x: 0, y: 0, k: 1 })}
            >
              <g transform={`translate(${view.x}, ${view.y}) scale(${view.k})`}>
              {/* Edges */}
              {visibleEdges.map((e) => {
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
                      strokeWidth={active ? 1.5 : 0.75}
                      strokeOpacity={active ? 0.9 : selected ? 0.06 : 0.18}
                    >
                      <title>{e.rel.replace(/_/g, " ")}</title>
                    </line>
                  </g>
                );
              })}
              {/* Nodes */}
              {visibleNodes.map((n) => {
                const p = layout.get(n.id);
                if (!p) return null;
                const c = kindColor(n.kind);
                const r = nodeRadius(n);
                const matched = matchIds ? matchIds.has(n.id) : null;
                const dimmed =
                  (matchIds && !matched) ||
                  (selected && n.id !== selected.id && !neighborIds.has(n.id));
                const accent = selectedId === n.id || matched === true;
                return (
                  <g
                    key={n.id}
                    transform={`translate(${p.x}, ${p.y})`}
                    className="cursor-pointer"
                    opacity={dimmed ? 0.18 : 1}
                    onClick={(ev) => {
                      ev.stopPropagation();
                      setSelectedId(n.id);
                    }}
                  >
                    <circle
                      r={r}
                      fill={c.fill}
                      stroke={accent ? "#F2541B" : c.stroke}
                      strokeWidth={accent ? 2.5 : 1.25}
                    />
                    {/* Only label when zoomed in, or for selected/neighbour/large
                        nodes — keeps a big graph from becoming a wall of text. */}
                    {(view.k >= 0.85 ||
                      selectedId === n.id ||
                      matched === true ||
                      (selected && neighborIds.has(n.id)) ||
                      r >= 16) && (
                      <text
                        y={r + 13}
                        textAnchor="middle"
                        fontSize={11 / Math.max(view.k, 1)}
                        fill="#6b6657"
                        style={{ pointerEvents: "none", userSelect: "none" }}
                      >
                        {n.name.length > 24 ? `${n.name.slice(0, 23)}…` : n.name}
                      </text>
                    )}
                    <title>{`${c.label}: ${n.name}`}</title>
                  </g>
                );
              })}
              </g>
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
