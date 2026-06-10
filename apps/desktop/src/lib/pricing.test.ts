/**
 * Tests for pricing.ts — model cost estimation.
 */
import { describe, expect, it } from "vitest";
import { estimateCost, formatCost, PRICING_TABLE } from "./pricing";
import type { NormalizedSession } from "./types";

// ─── helpers ─────────────────────────────────────────────────────────────────

function makeSession(
  overrides: Partial<NormalizedSession> & { id?: string } = {}
): NormalizedSession {
  return {
    id: "sess-1",
    tool: "claude-code",
    title: "Test session",
    messageCount: 5,
    messages: [],
    models: [],
    preview: "test",
    filePath: "/tmp/sess-1.jsonl",
    ...overrides,
  };
}

// ─── PRICING_TABLE ────────────────────────────────────────────────────────────

describe("PRICING_TABLE", () => {
  it("contains at least one claude entry", () => {
    const keys = Object.keys(PRICING_TABLE);
    expect(keys.some((k) => k.includes("claude"))).toBe(true);
  });

  it("has positive input/output prices for every entry", () => {
    for (const [model, entry] of Object.entries(PRICING_TABLE)) {
      expect(entry.inputPerMtok).toBeGreaterThan(0);
      expect(entry.outputPerMtok).toBeGreaterThan(0);
    }
  });
});

// ─── estimateCost ─────────────────────────────────────────────────────────────

describe("estimateCost", () => {
  it("returns usd=0 and known=true for a session with no tokens", () => {
    const result = estimateCost(makeSession());
    expect(result.usd).toBe(0);
    expect(result.known).toBe(true);
  });

  it("estimates cost for claude-sonnet-4 (known model)", () => {
    const session = makeSession({
      models: ["claude-sonnet-4-5"],
      tokens: { input: 1_000_000, output: 500_000 },
    });
    const { usd, known } = estimateCost(session);
    expect(known).toBe(true);
    expect(usd).toBeGreaterThan(0);
    // claude-sonnet-4.5: $3/Mtok input, $15/Mtok output → 1M*3 + 0.5M*15 = 3 + 7.5 = 10.5
    expect(usd).toBeCloseTo(10.5, 2);
  });

  it("estimates cost for claude-opus-4 (known model)", () => {
    const session = makeSession({
      models: ["claude-opus-4-5"],
      tokens: { input: 1_000_000, output: 1_000_000 },
    });
    const { usd, known } = estimateCost(session);
    expect(known).toBe(true);
    expect(usd).toBeGreaterThan(0);
  });

  it("estimates cost for claude-haiku-4 (known model)", () => {
    const session = makeSession({
      models: ["claude-haiku-4-5"],
      tokens: { input: 1_000_000, output: 1_000_000 },
    });
    const { usd, known } = estimateCost(session);
    expect(known).toBe(true);
    expect(usd).toBeGreaterThan(0);
  });

  it("uses default rate for unknown models and sets known=false", () => {
    const session = makeSession({
      models: ["some-unknown-model-xyz"],
      tokens: { input: 1_000_000, output: 1_000_000 },
    });
    const { usd, known } = estimateCost(session);
    expect(known).toBe(false);
    expect(usd).toBeGreaterThan(0);
  });

  it("uses the first recognised model in a multi-model session", () => {
    // First model is known, second is not — should use the known one
    const knownSession = makeSession({
      models: ["claude-sonnet-4-5", "some-unknown"],
      tokens: { input: 1_000_000, output: 0 },
    });
    const unknownSession = makeSession({
      models: ["some-unknown"],
      tokens: { input: 1_000_000, output: 0 },
    });
    const known = estimateCost(knownSession);
    const unknown = estimateCost(unknownSession);
    expect(known.known).toBe(true);
    expect(unknown.known).toBe(false);
    // Known model should cost more than default (since claude-sonnet-4.5 is $3/Mtok vs default $1/Mtok)
    expect(known.usd).toBeGreaterThan(unknown.usd);
  });

  it("picks the most expensive known model from multi-model list", () => {
    // When multiple known models exist, should pick the priciest
    const session = makeSession({
      models: ["claude-haiku-4-5", "claude-opus-4-5"],
      tokens: { input: 1_000_000, output: 0 },
    });
    const { usd, known } = estimateCost(session);
    expect(known).toBe(true);
    // Should pick opus pricing ($15/Mtok) not haiku ($0.25/Mtok)
    const haiku = PRICING_TABLE["claude-haiku-4-5"];
    const opus = PRICING_TABLE["claude-opus-4-5"];
    if (haiku && opus) {
      expect(usd).toBeCloseTo(opus.inputPerMtok, 0);
    }
  });

  it("estimates gpt-4o correctly", () => {
    const session = makeSession({
      models: ["gpt-4o"],
      tokens: { input: 1_000_000, output: 0 },
    });
    const { usd, known } = estimateCost(session);
    expect(known).toBe(true);
    expect(usd).toBeGreaterThan(0);
  });
});

// ─── formatCost ───────────────────────────────────────────────────────────────

describe("formatCost", () => {
  it("formats zero cost", () => {
    expect(formatCost({ usd: 0, known: true })).toBe("$0.00");
  });

  it("prepends ~ for unknown model estimates", () => {
    expect(formatCost({ usd: 1.5, known: false })).toBe("~$1.50");
  });

  it("uses 4 decimal places for sub-cent amounts", () => {
    expect(formatCost({ usd: 0.005, known: true })).toBe("$0.0050");
  });

  it("uses 2 decimal places for amounts >= 1 cent", () => {
    expect(formatCost({ usd: 0.01, known: true })).toBe("$0.01");
    expect(formatCost({ usd: 12.345, known: true })).toBe("$12.35");
  });

  it("prepends ~ for unknown sub-cent estimates", () => {
    expect(formatCost({ usd: 0.0001, known: false })).toBe("~$0.0001");
  });
});
