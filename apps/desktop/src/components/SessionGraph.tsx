import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Share2, ExternalLink, Loader2 } from "lucide-react";
import { useSettings } from "@/store/settings";
import { makeApiClient } from "@/lib/api/client";
import { GraphNodePanel } from "@/components/GraphNodePanel";
import {
  computeForceLayout,
  kindColor,
  nodeRadius,
  type GraphData,
  type GraphNode,
} from "@/lib/graph";

const W = 600;
const H = 260;

/**
 * Compact knowledge-graph render of a single session's subgraph.
 *
 * Fetches GET /v1/graph/session/{id} and draws the nodes/edges this session
 * produced. ``same_as`` edges — the cross-session links written by entity
 * resolution — are drawn in the brand accent so you can see where this
 * session connects out to others. Falls back gracefully when the hub is not
 * configured or the session has no graph yet.
 */
export function SessionGraph({ sessionId }: { sessionId: string }) {
  const settings = useSettings();
  const navigate = useNavigate();
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    if (!settings.apiBaseUrl) return;
    const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
    try {
      const g = await client.getSessionGraph(sessionId);
      setData(g);
      // A rename/merge/delete may have removed the selected node.
      setSelectedId((cur) => (cur && !g.nodes.some((n) => n.id === cur) ? null : cur));
    } catch {
      /* keep current view */
    }
  }, [sessionId, settings.apiBaseUrl, settings.apiKey]);

  useEffect(() => {
    let cancelled = false;
    if (!settings.apiBaseUrl) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setBuilding(false);
    setError(false);
    const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");

    (async () => {
      try {
        let g = await client.getSessionGraph(sessionId);
        // Not built yet → build this one session on demand (fast: ~50ms), refetch.
        if (!cancelled && g.nodes.length === 0) {
          setBuilding(true);
          try {
            await client.buildSessionGraph(sessionId);
            g = await client.getSessionGraph(sessionId);
          } catch {
            /* leave empty */
          }
          if (!cancelled) setBuilding(false);
        }
        if (!cancelled) setData(g);
      } catch {
        if (!cancelled) setError(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sessionId, settings.apiBaseUrl, settings.apiKey]);

  const layout = useMemo(() => {
    if (!data) return new Map<string, { x: number; y: number }>();
    return computeForceLayout(data.nodes, data.edges, { width: W, height: H });
  }, [data]);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphData["nodes"][number]>();
    for (const n of data?.nodes ?? []) m.set(n.id, n);
    return m;
  }, [data]);

  // Not connected to a hub, or the fetch failed — say nothing rather than show a broken box.
  if (!settings.apiBaseUrl || error) return null;

  const isEmpty = !loading && (data?.nodes.length ?? 0) === 0;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-micro text-ink-faint uppercase tracking-wide">
          Knowledge graph
        </span>
        {!isEmpty && (
          <button
            onClick={() => navigate("/graph")}
            className="flex items-center gap-1 text-micro text-accent hover:text-accent-ink transition-colors duration-120"
          >
            Full graph
            <ExternalLink size={10} />
          </button>
        )}
      </div>

      {loading || building ? (
        <div className="h-[200px] rounded-card border border-border bg-bg flex flex-col items-center justify-center gap-3">
          <Loader2 size={28} className="text-accent animate-spin" />
          <span className="text-small text-ink-soft">
            {building ? "Building this session's graph…" : "Loading graph…"}
          </span>
        </div>
      ) : isEmpty ? (
        <p className="text-small text-ink-faint italic">
          No graph extracted for this session yet.
        </p>
      ) : (
        <div className="rounded-card border border-border bg-bg overflow-hidden">
          <svg
            width="100%"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label="Session knowledge graph"
          >
            {/* Edges — same_as in accent (cross-session links), others muted. */}
            {data!.edges.map((e) => {
              const a = layout.get(e.src);
              const b = layout.get(e.dst);
              if (!a || !b) return null;
              const isSameAs = e.rel === "same_as";
              return (
                <line
                  key={e.id}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={isSameAs ? "#F2541B" : "#D8D2C4"}
                  strokeWidth={isSameAs ? 1.5 : 1}
                  strokeDasharray={isSameAs ? "4 3" : undefined}
                  strokeOpacity={0.85}
                >
                  <title>{e.rel.replace(/_/g, " ")}</title>
                </line>
              );
            })}
            {/* Nodes */}
            {data!.nodes.map((n) => {
              const p = layout.get(n.id);
              if (!p) return null;
              const c = kindColor(n.kind);
              const r = nodeRadius(n);
              return (
                <g
                  key={n.id}
                  transform={`translate(${p.x}, ${p.y})`}
                  className="cursor-pointer"
                  onClick={() => setSelectedId(n.id)}
                >
                  <circle
                    r={r}
                    fill={c.fill}
                    stroke={selectedId === n.id ? "#F2541B" : c.stroke}
                    strokeWidth={selectedId === n.id ? 2.5 : 1.25}
                  />
                  <text
                    y={r + 11}
                    textAnchor="middle"
                    fontSize={10}
                    fill="#6b6657"
                    style={{ pointerEvents: "none", userSelect: "none" }}
                  >
                    {n.name.length > 20 ? `${n.name.slice(0, 19)}…` : n.name}
                  </text>
                  <title>{`${c.label}: ${n.name}`}</title>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      {/* Node inspector/editor for the clicked node (same panel as GraphPage). */}
      {selectedId && data && nodeById.get(selectedId) && (
        <div className="flex justify-end">
          <GraphNodePanel
            node={nodeById.get(selectedId) as GraphNode}
            edges={data.edges.filter((e) => e.src === selectedId || e.dst === selectedId)}
            nodeById={nodeById}
            allNodes={data.nodes}
            localSessionIds={new Set([sessionId])}
            client={makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "")}
            onClose={() => setSelectedId(null)}
            onSelect={(id) => setSelectedId(id)}
            onChanged={() => void refetch()}
          />
        </div>
      )}

      {/* Hint shown only when this session has cross-session links. This is a
          sibling of the loading/empty branch above, so it must guard `data`
          itself — during the initial load data is null while loading is true. */}
      {!isEmpty && data != null && data.edges.some((e) => e.rel === "same_as") && (
        <p className="flex items-center gap-1.5 text-micro text-ink-faint">
          <Share2 size={11} className="text-accent" />
          Dashed accent links connect this session to others naming the same concept.
        </p>
      )}
    </div>
  );
}
