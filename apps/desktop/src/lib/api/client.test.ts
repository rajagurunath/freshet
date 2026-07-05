/**
 * Regression: GET /v1/sessions returns a paginated envelope
 * `{items, total, limit, offset}` since the v2 ingest-hardening work.
 * listHubSessions used to expect a bare array — `.map` threw on the object
 * and HubPage rendered "Could not reach the hub" with the server up.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeApiClient } from "./client";

const PAGE_RESPONSE = {
  items: [
    {
      id: "s1",
      tool: "claude-code",
      title: "Fix auth race",
      category: "engineering",
      author: "u1",
      team: null,
      project: "api",
      visibility: "company",
      message_count: 12,
      models: ["claude-sonnet-4-6"],
      preview: "p",
      created_at: "2026-06-10T10:00:00Z",
      blob_uri: "file:///tmp/s1.json",
      summary: null,
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
};

function mockFetchOnce(payload: unknown) {
  const fn = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("shareSession", () => {
  it("POSTs to /v1/sessions/{id}/share and returns the url", async () => {
    const fn = mockFetchOnce({
      url: "https://hub.example.com/c/s1?t=tok&expiry=9999",
      token: "tok",
      expiry: 9999,
    });
    const client = makeApiClient("http://localhost:8787", "dev-key");
    const result = await client.shareSession("s1");
    expect(result.url).toBe("https://hub.example.com/c/s1?t=tok&expiry=9999");
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8787/v1/sessions/s1/share");
    expect((init as RequestInit).method).toBe("POST");
  });
});

describe("backfillGraph", () => {
  it("POSTs to /v1/graph/backfill and returns enqueued/skipped counts", async () => {
    const fn = mockFetchOnce({ enqueued: 3, skipped: 1 });
    const client = makeApiClient("http://localhost:8787", "dev-key");
    const result = await client.backfillGraph();
    expect(result.enqueued).toBe(3);
    expect(result.skipped).toBe(1);
    const [url, init] = fn.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8787/v1/graph/backfill");
    expect((init as RequestInit).method).toBe("POST");
  });
});

describe("listHubSessions", () => {
  it("unwraps the paginated {items} envelope from GET /v1/sessions", async () => {
    mockFetchOnce(PAGE_RESPONSE);
    const client = makeApiClient("http://localhost:8787", "dev-key");
    const sessions = await client.listHubSessions();
    expect(sessions).toHaveLength(1);
    expect(sessions[0].id).toBe("s1");
    expect(sessions[0].messageCount).toBe(12);
    expect(sessions[0].startedAt).toBe("2026-06-10T10:00:00Z");
  });

  it("still accepts a bare array (older servers)", async () => {
    mockFetchOnce(PAGE_RESPONSE.items);
    const client = makeApiClient("http://localhost:8787", "dev-key");
    const sessions = await client.listHubSessions();
    expect(sessions).toHaveLength(1);
    expect(sessions[0].id).toBe("s1");
  });
});

describe("graph curation", () => {
  it("PATCHes /v1/graph/nodes/{id} with the edit body", async () => {
    const fn = mockFetchOnce({ id: "n1", merged: false, node: null });
    const client = makeApiClient("http://hub", "k");
    const res = await client.updateGraphNode("n1", { name: "checkout" });
    expect(res).toEqual({ id: "n1", merged: false, node: null });
    const [url, init] = fn.mock.calls[0];
    expect(url).toBe("http://hub/v1/graph/nodes/n1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "checkout" });
  });

  it("DELETEs nodes and edges", async () => {
    let fn = mockFetchOnce({ deleted: true });
    const client = makeApiClient("http://hub", "k");
    await client.deleteGraphNode("n1");
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/nodes/n1");
    expect(fn.mock.calls[0][1].method).toBe("DELETE");
    fn = mockFetchOnce({ deleted: true });
    await client.deleteGraphEdge("e1");
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/edges/e1");
  });

  it("POSTs new nodes and edges", async () => {
    let fn = mockFetchOnce({ id: "n9" });
    const client = makeApiClient("http://hub", "k");
    await client.createGraphNode({ kind: "decision", name: "sqlite over kuzudb" });
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/nodes");
    expect(fn.mock.calls[0][1].method).toBe("POST");
    fn = mockFetchOnce({ id: "e9" });
    await client.createGraphEdge({ src: "a", dst: "b", rel: "uses" });
    expect(fn.mock.calls[0][0]).toBe("http://hub/v1/graph/edges");
  });
});
