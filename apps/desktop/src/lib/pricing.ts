/**
 * Model cost estimation helpers.
 *
 * Prices are in USD per 1 million tokens (per-Mtok).
 * Sources: Anthropic pricing page (Jun 2026), OpenAI pricing page (Jun 2026).
 * Unknown models fall back to DEFAULT_RATE; estimates are flagged with
 * `known: false` so the UI can render them with a "~" prefix.
 */
import type { NormalizedSession } from "./types";

export interface ModelPricing {
  inputPerMtok: number;
  outputPerMtok: number;
}

/** Canonical model id → per-Mtok pricing. */
export const PRICING_TABLE: Record<string, ModelPricing> = {
  // ── Anthropic Claude 4.x ──────────────────────────────────────────────────
  "claude-opus-4-5":   { inputPerMtok: 15,   outputPerMtok: 75   },
  "claude-opus-4":     { inputPerMtok: 15,   outputPerMtok: 75   },
  "claude-sonnet-4-5": { inputPerMtok: 3,    outputPerMtok: 15   },
  "claude-sonnet-4":   { inputPerMtok: 3,    outputPerMtok: 15   },
  "claude-haiku-4-5":  { inputPerMtok: 0.25, outputPerMtok: 1.25 },
  "claude-haiku-4":    { inputPerMtok: 0.25, outputPerMtok: 1.25 },
  // ── Anthropic Claude 3.x (legacy) ─────────────────────────────────────────
  "claude-3-5-sonnet-20241022": { inputPerMtok: 3,    outputPerMtok: 15   },
  "claude-3-5-haiku-20241022":  { inputPerMtok: 0.8,  outputPerMtok: 4    },
  "claude-3-opus-20240229":     { inputPerMtok: 15,   outputPerMtok: 75   },
  "claude-3-sonnet-20240229":   { inputPerMtok: 3,    outputPerMtok: 15   },
  "claude-3-haiku-20240307":    { inputPerMtok: 0.25, outputPerMtok: 1.25 },
  // ── OpenAI GPT-4o / GPT-5.x ───────────────────────────────────────────────
  "gpt-4o":         { inputPerMtok: 2.5,  outputPerMtok: 10  },
  "gpt-4o-mini":    { inputPerMtok: 0.15, outputPerMtok: 0.6 },
  "gpt-4-turbo":    { inputPerMtok: 10,   outputPerMtok: 30  },
  "gpt-4":          { inputPerMtok: 30,   outputPerMtok: 60  },
  "gpt-3.5-turbo":  { inputPerMtok: 0.5,  outputPerMtok: 1.5 },
  "o1":             { inputPerMtok: 15,   outputPerMtok: 60  },
  "o1-mini":        { inputPerMtok: 3,    outputPerMtok: 12  },
  "o3":             { inputPerMtok: 10,   outputPerMtok: 40  },
  "o3-mini":        { inputPerMtok: 1.1,  outputPerMtok: 4.4 },
  "o4-mini":        { inputPerMtok: 1.1,  outputPerMtok: 4.4 },
};

/** Default rate used for unknown models (conservative estimate). */
const DEFAULT_RATE: ModelPricing = { inputPerMtok: 1, outputPerMtok: 4 };

/**
 * Resolve the best pricing entry for a list of model ids.
 *
 * Strategy:
 * 1. Try an exact match for each model id.
 * 2. Try prefix matching (e.g. "claude-sonnet-4-5-20250514" → "claude-sonnet-4-5").
 * 3. Among all resolved entries pick the most expensive (input+output combined)
 *    — a proxy for "the model that drove the bill".
 * 4. If none match → `null` (caller uses DEFAULT_RATE).
 */
function resolveModelPricing(models: string[]): ModelPricing | null {
  let best: ModelPricing | null = null;
  let bestCombined = 0;

  for (const raw of models) {
    const id = raw.toLowerCase().trim();
    // Exact match
    let entry = PRICING_TABLE[id];
    // Prefix match (versioned suffixes like "-20251022")
    if (!entry) {
      for (const key of Object.keys(PRICING_TABLE)) {
        if (id.startsWith(key)) {
          entry = PRICING_TABLE[key]!;
          break;
        }
      }
    }
    if (entry) {
      const combined = entry.inputPerMtok + entry.outputPerMtok;
      if (combined > bestCombined) {
        best = entry;
        bestCombined = combined;
      }
    }
  }

  return best;
}

export interface CostEstimate {
  /** Estimated total USD cost. */
  usd: number;
  /**
   * `true` when at least one model in the session has a known price entry.
   * `false` means the estimate used the default rate — render with "~" prefix.
   */
  known: boolean;
}

/**
 * Estimate the total USD cost for a session based on its token counts and
 * the models that were used.
 */
export function estimateCost(session: NormalizedSession): CostEstimate {
  const tokens = session.tokens;
  const input = tokens?.input ?? 0;
  const output = tokens?.output ?? 0;

  if (input === 0 && output === 0) {
    return { usd: 0, known: true };
  }

  const models = session.models ?? [];
  const pricing = resolveModelPricing(models);
  const known = pricing !== null;
  const rate = pricing ?? DEFAULT_RATE;

  const usd =
    (input * rate.inputPerMtok) / 1_000_000 +
    (output * rate.outputPerMtok) / 1_000_000;

  return { usd, known };
}

/**
 * Format a cost estimate for display.
 * Prepends "~" for unknown-model estimates.
 * Uses $ with 4 decimal places for sub-cent amounts, 2 decimal places otherwise.
 */
export function formatCost(estimate: CostEstimate): string {
  const prefix = estimate.known ? "" : "~";
  const { usd } = estimate;
  if (usd === 0) return `${prefix}$0.00`;
  if (usd < 0.01) return `${prefix}$${usd.toFixed(4)}`;
  return `${prefix}$${usd.toFixed(2)}`;
}
