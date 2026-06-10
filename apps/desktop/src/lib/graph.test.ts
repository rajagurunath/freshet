import { describe, it, expect } from "vitest";
import {
  computeForceLayout,
  kindColor,
  GRAPH_KIND_COLORS,
  type GraphNode,
  type GraphEdge,
} from "./graph";

function node(id: string, kind = "feature"): GraphNode {
  return { id, kind, name: id, sessionIds: [] };
}

function edge(src: string, dst: string): GraphEdge {
  return { id: `${src}->${dst}`, src, dst, rel: "relates_to", weight: 1 };
}

const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.hypot(a.x - b.x, a.y - b.y);

describe("computeForceLayout", () => {
  it("returns an empty map for no nodes", () => {
    const layout = computeForceLayout([], [], { width: 800, height: 600 });
    expect(layout.size).toBe(0);
  });

  it("centers a single node", () => {
    const layout = computeForceLayout([node("a")], [], { width: 800, height: 600 });
    const p = layout.get("a")!;
    expect(p.x).toBeCloseTo(400, 0);
    expect(p.y).toBeCloseTo(300, 0);
  });

  it("keeps every node inside the bounds (with padding)", () => {
    const nodes = Array.from({ length: 30 }, (_, i) => node(`n${i}`));
    const edges = Array.from({ length: 15 }, (_, i) => edge(`n${i}`, `n${i + 15}`));
    const layout = computeForceLayout(nodes, edges, { width: 800, height: 600 });
    expect(layout.size).toBe(30);
    for (const p of layout.values()) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(800);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(600);
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
    }
  });

  it("pulls connected nodes closer than unconnected ones", () => {
    // Two connected pairs (a-b, c-d) with no cross edges: spring attraction
    // should make each pair tighter than any cross-pair distance.
    const nodes = [node("a"), node("b"), node("c"), node("d")];
    const edges = [edge("a", "b"), edge("c", "d")];
    const layout = computeForceLayout(nodes, edges, { width: 800, height: 600 });
    const a = layout.get("a")!;
    const b = layout.get("b")!;
    const c = layout.get("c")!;
    const d = layout.get("d")!;
    expect(dist(a, b)).toBeLessThan(dist(a, c));
    expect(dist(a, b)).toBeLessThan(dist(a, d));
    expect(dist(c, d)).toBeLessThan(dist(c, b));
  });

  it("separates unconnected nodes (repulsion)", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const layout = computeForceLayout(nodes, [], { width: 800, height: 600 });
    const pts = [...layout.values()];
    expect(dist(pts[0], pts[1])).toBeGreaterThan(40);
    expect(dist(pts[0], pts[2])).toBeGreaterThan(40);
    expect(dist(pts[1], pts[2])).toBeGreaterThan(40);
  });

  it("is deterministic for identical input", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const edges = [edge("a", "b")];
    const l1 = computeForceLayout(nodes, edges, { width: 640, height: 480 });
    const l2 = computeForceLayout(nodes, edges, { width: 640, height: 480 });
    for (const id of ["a", "b", "c"]) {
      expect(l1.get(id)).toEqual(l2.get(id));
    }
  });

  it("ignores edges that reference unknown nodes", () => {
    const nodes = [node("a"), node("b")];
    const edges = [edge("a", "ghost")];
    const layout = computeForceLayout(nodes, edges, { width: 800, height: 600 });
    expect(layout.size).toBe(2);
    for (const p of layout.values()) {
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
    }
  });
});

describe("kindColor", () => {
  it("has a color for every known node kind", () => {
    for (const kind of ["repo", "service", "feature", "person", "decision", "tool", "pr"]) {
      expect(GRAPH_KIND_COLORS[kind]).toBeDefined();
      expect(kindColor(kind).fill).toMatch(/^#/);
    }
  });

  it("falls back to a default color for unknown kinds", () => {
    expect(kindColor("whatever")).toEqual(GRAPH_KIND_COLORS.default);
  });
});
