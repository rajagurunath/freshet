import { describe, it, expect, vi, afterEach } from "vitest";
import { groupRulesByStatus, rulesToMarkdown, type Rule } from "./rules";
import { makeApiClient } from "./api/client";

function rule(over: Partial<Rule> = {}): Rule {
  return {
    id: "r1",
    text: "Use conventional commit messages",
    rationale: "Seen across 4 sessions",
    evidence: ["s1", "s2"],
    scope: null,
    status: "proposed",
    author: "guru",
    createdAt: "2026-06-10T10:00:00Z",
    updatedAt: null,
    ...over,
  };
}

// ─── groupRulesByStatus ──────────────────────────────────────────────────────

describe("groupRulesByStatus", () => {
  it("splits rules into proposed / accepted / rejected buckets", () => {
    const rules = [
      rule({ id: "a", status: "proposed" }),
      rule({ id: "b", status: "accepted" }),
      rule({ id: "c", status: "rejected" }),
      rule({ id: "d", status: "proposed" }),
    ];
    const g = groupRulesByStatus(rules);
    expect(g.proposed.map((r) => r.id)).toContain("a");
    expect(g.proposed.map((r) => r.id)).toContain("d");
    expect(g.accepted.map((r) => r.id)).toEqual(["b"]);
    expect(g.rejected.map((r) => r.id)).toEqual(["c"]);
  });

  it("sorts each bucket newest-first by createdAt", () => {
    const rules = [
      rule({ id: "old", createdAt: "2026-06-01T00:00:00Z" }),
      rule({ id: "new", createdAt: "2026-06-09T00:00:00Z" }),
      rule({ id: "mid", createdAt: "2026-06-05T00:00:00Z" }),
    ];
    const g = groupRulesByStatus(rules);
    expect(g.proposed.map((r) => r.id)).toEqual(["new", "mid", "old"]);
  });

  it("returns empty buckets for no rules", () => {
    const g = groupRulesByStatus([]);
    expect(g.proposed).toEqual([]);
    expect(g.accepted).toEqual([]);
    expect(g.rejected).toEqual([]);
  });
});

// ─── rulesToMarkdown ─────────────────────────────────────────────────────────

describe("rulesToMarkdown", () => {
  it("renders only accepted rules as a CLAUDE.md-style block", () => {
    const md = rulesToMarkdown([
      rule({ id: "a", status: "accepted", text: "Prefer pytest over unittest" }),
      rule({ id: "b", status: "proposed", text: "NEVER appears" }),
      rule({ id: "c", status: "rejected", text: "ALSO never appears" }),
    ]);
    expect(md).toContain("# Rules");
    expect(md).toContain("- Prefer pytest over unittest");
    expect(md).not.toContain("NEVER appears");
    expect(md).not.toContain("ALSO never appears");
  });

  it("includes the rationale as an italic sub-line when present", () => {
    const md = rulesToMarkdown([
      rule({ status: "accepted", text: "Squash before merge", rationale: "Observed in 3 repos" }),
    ]);
    expect(md).toContain("- Squash before merge");
    expect(md).toContain("_Observed in 3 repos_");
  });

  it("omits the rationale line when absent", () => {
    const md = rulesToMarkdown([
      rule({ status: "accepted", text: "Squash before merge", rationale: null }),
    ]);
    expect(md).not.toContain("__");
    expect(md.trim().endsWith("- Squash before merge")).toBe(true);
  });

  it("returns a placeholder block when no rules are accepted", () => {
    const md = rulesToMarkdown([rule({ status: "proposed" })]);
    expect(md).toContain("# Rules");
    expect(md).toContain("No accepted rules yet");
  });
});

// ─── ApiClient rules methods ─────────────────────────────────────────────────

type FetchCall = { url: string; init: RequestInit | undefined };

function stubFetch(body: unknown, opts: { text?: string; status?: number } = {}) {
  const calls: FetchCall[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: (opts.status ?? 200) < 400,
      status: opts.status ?? 200,
      json: async () => body,
      text: async () => opts.text ?? JSON.stringify(body),
    } as unknown as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const wireRule = {
  id: "r1",
  text: "Use uv for python deps",
  rationale: "Seen in 5 sessions",
  evidence: ["s1"],
  scope: null,
  status: "proposed",
  author: "guru",
  created_at: "2026-06-10T10:00:00Z",
  updated_at: null,
};

describe("ApiClient rules methods", () => {
  it("listRules hits GET /v1/rules with snake_case params and camelCases the page", async () => {
    const calls = stubFetch({ items: [wireRule], total: 1, limit: 100, offset: 0 });
    const client = makeApiClient("http://hub.test", "k1");
    const page = await client.listRules({ status: "proposed", limit: 50, offset: 10 });

    expect(calls[0].url).toContain("http://hub.test/v1/rules?");
    expect(calls[0].url).toContain("status=proposed");
    expect(calls[0].url).toContain("limit=50");
    expect(calls[0].url).toContain("offset=10");
    expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer k1");
    expect(page.total).toBe(1);
    expect(page.items[0].createdAt).toBe("2026-06-10T10:00:00Z");
    expect(page.items[0].evidence).toEqual(["s1"]);
  });

  it("acceptRule POSTs to /v1/rules/{id}/accept and returns the camelCased rule", async () => {
    const calls = stubFetch({ ...wireRule, status: "accepted" });
    const client = makeApiClient("http://hub.test", "k1");
    const r = await client.acceptRule("r1");

    expect(calls[0].url).toBe("http://hub.test/v1/rules/r1/accept");
    expect(calls[0].init?.method).toBe("POST");
    expect(r.status).toBe("accepted");
    expect(r.createdAt).toBe("2026-06-10T10:00:00Z");
  });

  it("rejectRule POSTs to /v1/rules/{id}/reject", async () => {
    const calls = stubFetch({ ...wireRule, status: "rejected" });
    const client = makeApiClient("http://hub.test", "k1");
    const r = await client.rejectRule("r1");

    expect(calls[0].url).toBe("http://hub.test/v1/rules/r1/reject");
    expect(calls[0].init?.method).toBe("POST");
    expect(r.status).toBe("rejected");
  });

  it("exportRules returns the raw markdown text from /v1/rules/export", async () => {
    stubFetch(null, { text: "# Rules\n\n- Use uv\n" });
    const client = makeApiClient("http://hub.test", "k1");
    const md = await client.exportRules();
    expect(md).toBe("# Rules\n\n- Use uv\n");
  });

  it("mineRules POSTs to /v1/rules/mine with snake_case query params", async () => {
    const calls = stubFetch({ job_id: "j42", kind: "rules_extract", author: "guru" });
    const client = makeApiClient("http://hub.test", "k1");
    const res = await client.mineRules({ nSessions: 30 });

    expect(calls[0].url).toContain("http://hub.test/v1/rules/mine?");
    expect(calls[0].url).toContain("n_sessions=30");
    expect(calls[0].init?.method).toBe("POST");
    expect(res.jobId).toBe("j42");
  });

  it("surfaces API errors with the detail message", async () => {
    stubFetch({ detail: "Rule 'nope' not found." }, { status: 404 });
    const client = makeApiClient("http://hub.test", "k1");
    await expect(client.acceptRule("nope")).rejects.toThrow("Rule 'nope' not found.");
  });
});
