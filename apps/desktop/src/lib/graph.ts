/**
 * Knowledge-graph types + dependency-free SVG force layout.
 *
 * Mirrors the API's GraphResponse (GET /v1/graph, GET /v1/graph/session/{id});
 * the ApiClient camelCases the wire format (session_ids → sessionIds).
 *
 * The layout is a small deterministic spring simulation (Fruchterman–Reingold
 * style): pairwise repulsion, spring attraction along edges, mild centering
 * gravity, simulated-annealing temperature cap, bounds clamp. No randomness —
 * initial positions sit on a golden-angle spiral so identical input always
 * yields an identical layout.
 */

// ─── types ───────────────────────────────────────────────────────────────────

export interface GraphNode {
  id: string;
  kind: string;
  name: string;
  summary?: string | null;
  visibility?: string | null;
  sessionIds: string[];
}

export interface GraphEdge {
  id: string;
  src: string;
  dst: string;
  rel: string;
  weight: number;
  sessionId?: string | null;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface LayoutOptions {
  width: number;
  height: number;
  /** Simulation steps. Default 250 — plenty for a few hundred nodes. */
  iterations?: number;
  /** Keep-out margin from the SVG edges. */
  padding?: number;
}

export interface Point {
  x: number;
  y: number;
}

// ─── node colors (DESIGN.md chip palette: muted fills, readable ink) ─────────

export interface KindColor {
  fill: string;
  stroke: string;
  ink: string;
  label: string;
}

export const GRAPH_KIND_COLORS: Record<string, KindColor> = {
  repo: { fill: "#eef0f4", stroke: "#c8cdd8", ink: "#3a4460", label: "Repo" },
  service: { fill: "#e6f4f4", stroke: "#a8d4d4", ink: "#1a6b6b", label: "Service" },
  feature: { fill: "#fdeee6", stroke: "#f2b59a", ink: "#b13d12", label: "Feature" },
  person: { fill: "#e8f5ef", stroke: "#b6dac7", ink: "#2e7d52", label: "Person" },
  decision: { fill: "#fef9e7", stroke: "#f0d080", ink: "#8a6d1a", label: "Decision" },
  tool: { fill: "#f0ecf7", stroke: "#cfc3e3", ink: "#5b4287", label: "Tool" },
  pr: { fill: "#e9f1f8", stroke: "#b9d2e8", ink: "#2b5e8c", label: "PR" },
  default: { fill: "#f1efe9", stroke: "#d8d4c8", ink: "#6b6657", label: "Node" },
};

export function kindColor(kind: string): KindColor {
  return GRAPH_KIND_COLORS[kind] ?? GRAPH_KIND_COLORS.default;
}

// ─── force layout ────────────────────────────────────────────────────────────

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5)); // ~2.39996 rad

export function computeForceLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  opts: LayoutOptions,
): Map<string, Point> {
  const { width, height } = opts;
  const iterations = opts.iterations ?? 250;
  const padding = opts.padding ?? 28;
  const n = nodes.length;

  const layout = new Map<string, Point>();
  if (n === 0) return layout;

  const cx = width / 2;
  const cy = height / 2;
  if (n === 1) {
    layout.set(nodes[0].id, { x: cx, y: cy });
    return layout;
  }

  // Deterministic initial positions: golden-angle spiral around the center.
  const spread = Math.min(width, height) / 2 - padding;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  const index = new Map<string, number>();
  for (let i = 0; i < n; i++) {
    index.set(nodes[i].id, i);
    const r = spread * Math.sqrt((i + 0.5) / n);
    const theta = i * GOLDEN_ANGLE;
    xs[i] = cx + r * Math.cos(theta);
    ys[i] = cy + r * Math.sin(theta);
  }

  // Edges as index pairs; silently drop edges referencing unknown nodes.
  const springs: Array<[number, number, number]> = [];
  for (const e of edges) {
    const a = index.get(e.src);
    const b = index.get(e.dst);
    if (a === undefined || b === undefined || a === b) continue;
    springs.push([a, b, e.weight > 0 ? e.weight : 1]);
  }

  // Ideal pairwise distance for the available area. Larger factor = more spread
  // so dense graphs (many co-occurrence edges) don't collapse into one ball.
  const k = Math.sqrt((width * height) / n) * 1.3;
  const dx = new Float64Array(n);
  const dy = new Float64Array(n);

  for (let iter = 0; iter < iterations; iter++) {
    dx.fill(0);
    dy.fill(0);

    // Repulsion: every pair pushes apart with k²/d.
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let ddx = xs[i] - xs[j];
        let ddy = ys[i] - ys[j];
        let d2 = ddx * ddx + ddy * ddy;
        if (d2 < 0.01) {
          // Coincident points: deterministic nudge along the index axis.
          ddx = 0.1 * (i - j);
          ddy = 0.1;
          d2 = ddx * ddx + ddy * ddy;
        }
        const d = Math.sqrt(d2);
        const force = (k * k) / d2;
        const fx = ddx / d * force;
        const fy = ddy / d * force;
        dx[i] += fx;
        dy[i] += fy;
        dx[j] -= fx;
        dy[j] -= fy;
      }
    }

    // Attraction along edges: spring force d²/k. Dampened and weight-capped so a
    // node with many co-occurrence edges isn't dragged into the center.
    for (const [a, b, w] of springs) {
      const ddx = xs[a] - xs[b];
      const ddy = ys[a] - ys[b];
      const d = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01;
      const force = ((d * d) / k) * Math.min(w, 2) * 0.45;
      const fx = (ddx / d) * force;
      const fy = (ddy / d) * force;
      dx[a] -= fx;
      dy[a] -= fy;
      dx[b] += fx;
      dy[b] += fy;
    }

    // Very gentle gravity — just enough to keep disconnected nodes on-canvas
    // without pulling everything into a tight clump.
    for (let i = 0; i < n; i++) {
      dx[i] += (cx - xs[i]) * 0.006;
      dy[i] += (cy - ys[i]) * 0.006;
    }

    // Cooling: cap displacement by a shrinking temperature, then clamp bounds.
    const temp = (Math.min(width, height) / 8) * (1 - iter / iterations) + 1;
    for (let i = 0; i < n; i++) {
      const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 1;
      const step = Math.min(d, temp);
      xs[i] += (dx[i] / d) * step;
      ys[i] += (dy[i] / d) * step;
      xs[i] = Math.min(width - padding, Math.max(padding, xs[i]));
      ys[i] = Math.min(height - padding, Math.max(padding, ys[i]));
    }
  }

  for (let i = 0; i < n; i++) {
    layout.set(nodes[i].id, { x: xs[i], y: ys[i] });
  }
  return layout;
}

/** Node radius scaled by how many sessions reference it (provenance count). */
export function nodeRadius(node: GraphNode): number {
  const s = node.sessionIds?.length ?? 0;
  return Math.min(22, 10 + Math.sqrt(s) * 3);
}
