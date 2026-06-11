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
