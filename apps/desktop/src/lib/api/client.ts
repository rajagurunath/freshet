/**
 * Typed HTTP client for the Freshet central API.
 *
 * The app speaks camelCase internally (see `../types`), but the API's documented
 * wire format is snake_case (see ARCHITECTURE.md). This client is the single
 * translation boundary: it deep-converts request bodies camelCase → snake_case
 * and responses snake_case → camelCase. Nothing else in the app needs to know.
 */
import type {
  NormalizedSession,
  PushEnvelope,
  QueryResponse,
  Citation,
  SessionSummary,
  Category,
  Visibility,
  Tool,
} from "../types";
import type { GraphData } from "../graph";
import type { Rule, RulePage, RuleStatus } from "../rules";

/**
 * A session record as returned by the hub's GET /v1/sessions endpoint.
 * Extends SessionSummary with hub-side metadata (category, visibility, author).
 */
export type HubSessionRecord = SessionSummary & {
  category: Category;
  visibility?: Visibility;
  author?: string;
  createdAt?: string;
  summary?: string;
};

// ─── filter types ─────────────────────────────────────────────────────────────

export interface GraphBuildProgress {
  done: number;
  total: number;
  running: boolean;
  started_at?: number;
  finished_at?: number;
}

export interface SessionFilters {
  category?: Category;
  tool?: Tool;
  project?: string;
  author?: string;
  visibility?: Visibility;
}

export interface QueryFilters {
  category?: Category;
  tool?: Tool;
  project?: string;
  author?: string;
}

export interface HubStats {
  totalSessions: number;
  totalChunks: number;
  byTool: Record<string, number>;
  byCategory: Record<string, number>;
}

/** An asset record as returned by the hub's /v1/assets endpoints (camelCased). */
export interface AssetRecord {
  id: string;
  kind: string;
  name: string;
  description: string;
  category: string;
  author: string;
  team?: string | null;
  visibility: Visibility;
  files: string[];
  blobUri: string;
  version: string;
  createdAt: string;
}

export interface AssetPage {
  items: AssetRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface ProviderInfo {
  id: string;
  label: string;
  available: boolean;
  isDefault: boolean;
  needsKey: boolean;
}

export interface ProvidersResponse {
  default: string;
  model: string;
  providers: ProviderInfo[];
}

// ─── AICP handoff types (already camelCase on the wire) ───────────────────────

/** One normalized transcript message (AICP camelCase wire twin of Message). */
export interface NormalizedMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  text: string;
  thinking?: string | null;
  toolName?: string | null;
  timestamp?: string | null;
  model?: string | null;
}

/** AICP L0 SessionManifest — lightweight, no message bodies. */
export interface SessionManifest {
  id: string;
  tool: string;
  title: string;
  project?: string | null;
  startedAt?: string | null;
  endedAt?: string | null;
  messageCount: number;
  tokens?: { input: number; output: number } | null;
  hasSummary: boolean;
  visibility: string;
  source: string;
}

export interface HandoffMore {
  grep: string;
  stream: string;
}

export interface HandoffDecision {
  decision: string;
  why?: string | null;
}

export interface HandoffWorkingSet {
  repos: string[];
  services: string[];
  libraries: string[];
}

export interface HandoffRelatedSession {
  id: string;
  title?: string | null;
  why?: string | null;
}

/**
 * The AICP HandoffPacket envelope (spec §6) plus Freshet's additive superset
 * keys. The envelope keys (protocol, session, summary, recent, more, issuedAt,
 * issuedBy, redacted) are exact; the rest are Freshet extensions.
 */
export interface HandoffPacket {
  // AICP envelope (exact)
  protocol: "aicp/0.1";
  session: SessionManifest;
  summary: string;
  recent: NormalizedMessage[];
  more: HandoffMore;
  issuedAt: string;
  issuedBy: string;
  redacted: boolean;
  // Freshet extension keys (additive superset)
  decisions: HandoffDecision[];
  touchedFiles: string[];
  workingSet: HandoffWorkingSet;
  relatedSessions: HandoffRelatedSession[];
  openThreads: string[];
  resumeHint: string;
}

// ─── case conversion ────────────────────────────────────────────────────────

const toSnake = (s: string) => s.replace(/[A-Z]/g, (m) => `_${m.toLowerCase()}`);
const toCamel = (s: string) => s.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());

function convertKeys<T = unknown>(value: unknown, fn: (k: string) => string): T {
  if (Array.isArray(value)) {
    return value.map((v) => convertKeys(v, fn)) as unknown as T;
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[fn(k)] = convertKeys(v, fn);
    }
    return out as T;
  }
  return value as T;
}

const snakeify = <T = unknown>(v: unknown): T => convertKeys<T>(v, toSnake);
const camelify = <T = unknown>(v: unknown): T => convertKeys<T>(v, toCamel);

// ─── client ──────────────────────────────────────────────────────────────────

export class ApiClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }

  private headers(): HeadersInit {
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${this.apiKey}`,
    };
  }

  /** Issues a request. Body is sent as-is (already snake-cased by callers). */
  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: this.headers(),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    if (!res.ok) {
      let message = `API error ${res.status}`;
      try {
        const json = (await res.json()) as { detail?: string; message?: string };
        message = json.detail ?? json.message ?? message;
      } catch {
        /* keep default */
      }
      throw new Error(message);
    }
    if (res.status === 204) return undefined as unknown as T;
    return res.json() as Promise<T>;
  }

  private buildQuery(filters: Record<string, unknown>): string {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== null) params.set(toSnake(k), String(v));
    }
    const q = params.toString();
    return q ? `?${q}` : "";
  }

  // ── public API ─────────────────────────────────────────────────────────────

  /** Returns true if the API is reachable and healthy. */
  async health(): Promise<boolean> {
    try {
      await this.request<unknown>("GET", "/healthz");
      return true;
    } catch {
      return false;
    }
  }

  /** Push a full session envelope to the hub (camelCase → snake_case on the wire). */
  async pushSession(env: PushEnvelope): Promise<{ id: string }> {
    const res = await this.request<{ session_id?: string; id?: string }>(
      "POST",
      "/v1/sessions",
      snakeify(env),
    );
    return { id: res.session_id ?? res.id ?? env.session.id };
  }

  /** List sessions from the hub catalog. */
  async listHubSessions(filters?: SessionFilters): Promise<HubSessionRecord[]> {
    const qs = filters ? this.buildQuery(filters as Record<string, unknown>) : "";
    const raw = await this.request<unknown>("GET", `/v1/sessions${qs}`);
    // v2 servers return a paginated envelope {items, total, limit, offset};
    // older servers returned a bare array. Accept both.
    const rows = Array.isArray(raw)
      ? raw
      : ((raw as { items?: unknown[] })?.items ?? []);
    return (camelify<HubSessionRecord[]>(rows) ?? []).map((r) => ({
      ...r,
      // catalog rows expose `createdAt`; surface it as startedAt for list views.
      startedAt: r.startedAt ?? r.createdAt,
    }));
  }

  /** List which LLM providers are usable on the server + the default. */
  async getProviders(): Promise<ProvidersResponse> {
    const raw = await this.request<{
      default: string;
      model: string;
      providers: Array<{
        id: string;
        label: string;
        available: boolean;
        is_default: boolean;
        needs_key: boolean;
      }>;
    }>("GET", "/v1/providers");
    return {
      default: raw.default,
      model: raw.model,
      providers: (raw.providers ?? []).map((p) => ({
        id: p.id,
        label: p.label,
        available: p.available,
        isDefault: p.is_default,
        needsKey: p.needs_key,
      })),
    };
  }

  /** Request an AI-generated summary of a session. */
  async summarize(
    session: NormalizedSession,
    provider?: string,
    model?: string,
  ): Promise<string> {
    const result = await this.request<{ summary: string }>("POST", "/v1/summarize", {
      session: snakeify(session),
      provider,
      model,
    });
    return result.summary;
  }

  /** Ask a question across the org knowledge base. */
  async query(
    question: string,
    filters?: QueryFilters,
    provider?: string,
    model?: string,
    useGraph?: boolean,
  ): Promise<QueryResponse> {
    const raw = await this.request<{ answer: string; citations: unknown[] }>(
      "POST",
      "/v1/query",
      {
        question,
        filters: filters ? snakeify(filters) : undefined,
        provider,
        model,
        use_graph: useGraph ?? false,
      },
    );
    return {
      answer: raw.answer,
      citations: camelify<Citation[]>(raw.citations) ?? [],
    };
  }

  /**
   * Pull the AICP HandoffPacket for a session — the live cross-agent handoff
   * bundle (envelope + decisions, touched files, working set, recent turns,
   * related sessions, resume hint). The AICP routes emit camelCase directly, so
   * (unlike the snake-case endpoints) the response is NOT run through camelify.
   * Resolves from disk first (live/unpushed sessions) then the hub catalog.
   */
  async getHandoff(
    sessionId: string,
    opts?: { levels?: string; n?: number },
  ): Promise<HandoffPacket> {
    const qs = this.buildQuery({
      levels: opts?.levels ?? "summary,recent",
      n: opts?.n ?? 20,
    });
    return this.request<HandoffPacket>(
      "GET",
      `/v1/session/${encodeURIComponent(sessionId)}/handoff${qs}`,
    );
  }

  /** Fetch the knowledge graph (optionally focused on a node's neighborhood). */
  async getGraph(opts?: { focus?: string; depth?: number }): Promise<GraphData> {
    const qs = opts ? this.buildQuery(opts as Record<string, unknown>) : "";
    const raw = await this.request<{ nodes: unknown[]; edges: unknown[] }>(
      "GET",
      `/v1/graph${qs}`,
    );
    return {
      nodes: camelify<GraphData["nodes"]>(raw.nodes) ?? [],
      edges: camelify<GraphData["edges"]>(raw.edges) ?? [],
    };
  }

  /** Ids of sessions that already have a knowledge graph extracted. */
  async getGraphSessionIds(): Promise<string[]> {
    const raw = await this.request<{ session_ids: string[] }>(
      "GET",
      "/v1/graph/sessions",
    );
    return raw.session_ids ?? [];
  }

  /** Start the offline build of graphs for all local sessions (idempotent). */
  async buildAllGraphs(): Promise<GraphBuildProgress> {
    return this.request<GraphBuildProgress>("POST", "/v1/graph/build-all");
  }

  /** Live progress of the offline graph build. */
  async getGraphBuildProgress(): Promise<GraphBuildProgress> {
    return this.request<GraphBuildProgress>("GET", "/v1/graph/build-progress");
  }

  /** Build the graph for a single session on demand. */
  async buildSessionGraph(sessionId: string): Promise<{ built: boolean }> {
    return this.request<{ built: boolean }>(
      "POST",
      `/v1/graph/build-session/${encodeURIComponent(sessionId)}`,
    );
  }

  /** Fetch the subgraph extracted from a single session. */
  async getSessionGraph(sessionId: string): Promise<GraphData> {
    const raw = await this.request<{ nodes: unknown[]; edges: unknown[] }>(
      "GET",
      `/v1/graph/session/${encodeURIComponent(sessionId)}`,
    );
    return {
      nodes: camelify<GraphData["nodes"]>(raw.nodes) ?? [],
      edges: camelify<GraphData["edges"]>(raw.edges) ?? [],
    };
  }

  /** Edit a graph node. Renaming onto an existing (kind,name) hard-merges. */
  async updateGraphNode(
    id: string,
    patch: { name?: string; kind?: string; summary?: string },
  ): Promise<{ id: string; merged: boolean; node?: unknown }> {
    return this.request("PATCH", `/v1/graph/nodes/${encodeURIComponent(id)}`, patch);
  }

  /** Delete a node; it will not be re-created by future extraction. */
  async deleteGraphNode(id: string): Promise<void> {
    await this.request("DELETE", `/v1/graph/nodes/${encodeURIComponent(id)}`);
  }

  /** Manually add a node. */
  async createGraphNode(node: {
    kind: string;
    name: string;
    summary?: string;
  }): Promise<{ id: string }> {
    return this.request("POST", "/v1/graph/nodes", node);
  }

  /** Manually link two existing nodes. */
  async createGraphEdge(edge: {
    src: string;
    dst: string;
    rel: string;
  }): Promise<{ id: string }> {
    return this.request("POST", "/v1/graph/edges", edge);
  }

  /** Delete an edge; it will not be re-created by future extraction. */
  async deleteGraphEdge(id: string): Promise<void> {
    await this.request("DELETE", `/v1/graph/edges/${encodeURIComponent(id)}`);
  }

  /** List extracted rules (optionally filtered by status/author). */
  async listRules(opts?: {
    status?: RuleStatus;
    author?: string;
    limit?: number;
    offset?: number;
  }): Promise<RulePage> {
    const qs = opts ? this.buildQuery(opts as Record<string, unknown>) : "";
    const raw = await this.request<unknown>("GET", `/v1/rules${qs}`);
    return camelify<RulePage>(raw);
  }

  /** Accept a proposed rule (explicit consent — enables export). */
  async acceptRule(ruleId: string): Promise<Rule> {
    const raw = await this.request<unknown>(
      "POST",
      `/v1/rules/${encodeURIComponent(ruleId)}/accept`,
    );
    return camelify<Rule>(raw);
  }

  /** Reject a proposed rule (never exported, not re-proposed). */
  async rejectRule(ruleId: string): Promise<Rule> {
    const raw = await this.request<unknown>(
      "POST",
      `/v1/rules/${encodeURIComponent(ruleId)}/reject`,
    );
    return camelify<Rule>(raw);
  }

  /** Export accepted rules as a CLAUDE.md-style markdown block (plain text). */
  async exportRules(): Promise<string> {
    const res = await fetch(`${this.baseUrl}/v1/rules/export`, {
      method: "GET",
      headers: this.headers(),
    });
    if (!res.ok) throw new Error(`API error ${res.status}`);
    return res.text();
  }

  /** Enqueue a rules-extraction job over the caller's recent sessions. */
  async mineRules(opts?: {
    nSessions?: number;
    author?: string;
    provider?: string;
    model?: string;
  }): Promise<{ jobId: string }> {
    const qs = opts ? this.buildQuery(opts as Record<string, unknown>) : "";
    const raw = await this.request<{ job_id: string }>("POST", `/v1/rules/mine${qs}`);
    return { jobId: raw.job_id };
  }

  /** List hub assets with optional kind/category filter and FTS search. */
  async listAssets(opts?: {
    kind?: string;
    category?: string;
    q?: string;
    limit?: number;
    offset?: number;
  }): Promise<AssetPage> {
    const qs = opts ? this.buildQuery(opts as Record<string, unknown>) : "";
    const raw = await this.request<unknown>("GET", `/v1/assets${qs}`);
    return camelify<AssetPage>(raw);
  }

  /** Upload an asset (skill/script/config/prompt) as a multipart ZIP. */
  async uploadAsset(
    meta: {
      kind: string;
      name: string;
      description?: string;
      category?: string;
      visibility?: string;
      version?: string;
    },
    zipData: Uint8Array,
    filename?: string,
  ): Promise<AssetRecord> {
    const form = new FormData();
    form.set("kind", meta.kind);
    form.set("name", meta.name);
    if (meta.description) form.set("description", meta.description);
    if (meta.category) form.set("category", meta.category);
    if (meta.visibility) form.set("visibility", meta.visibility);
    if (meta.version) form.set("version", meta.version);
    form.set(
      "file",
      new Blob([zipData as BlobPart], { type: "application/zip" }),
      filename ?? `${meta.name.replace(/\//g, "__")}.zip`,
    );
    // Multipart: let the browser set the Content-Type boundary itself.
    const res = await fetch(`${this.baseUrl}/v1/assets`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.apiKey}` },
      body: form,
    });
    if (!res.ok) {
      let message = `API error ${res.status}`;
      try {
        const json = (await res.json()) as { detail?: string; message?: string };
        message = json.detail ?? json.message ?? message;
      } catch {
        /* keep default */
      }
      throw new Error(message);
    }
    return camelify<AssetRecord>(await res.json());
  }

  /** Download an asset's ZIP payload as a Blob. */
  async downloadAsset(assetId: string): Promise<Blob> {
    const res = await fetch(
      `${this.baseUrl}/v1/assets/${encodeURIComponent(assetId)}/download`,
      { headers: { Authorization: `Bearer ${this.apiKey}` } },
    );
    if (!res.ok) throw new Error(`API error ${res.status}`);
    return res.blob();
  }

  /** Mint a short-lived HMAC share link for a session context page. */
  async shareSession(sessionId: string): Promise<{ url: string }> {
    const raw = await this.request<{ url: string; token: string; expiry: number }>(
      "POST",
      `/v1/sessions/${encodeURIComponent(sessionId)}/share`,
    );
    return { url: raw.url };
  }

  /** Trigger knowledge-graph backfill for all unextracted sessions. */
  async backfillGraph(): Promise<{ enqueued: number; skipped: number }> {
    const raw = await this.request<{ enqueued: number; skipped: number }>(
      "POST",
      "/v1/graph/backfill",
    );
    return { enqueued: raw.enqueued, skipped: raw.skipped };
  }

  /** Link existing session graphs by enqueuing cross-session entity resolution. */
  async resolveBackfillGraph(): Promise<{ enqueued: number; skipped: number }> {
    const raw = await this.request<{ enqueued: number; skipped: number }>(
      "POST",
      "/v1/graph/resolve-backfill",
    );
    return { enqueued: raw.enqueued, skipped: raw.skipped };
  }

  /** Retrieve hub-level statistics, normalized to the app's shape. */
  async stats(): Promise<HubStats> {
    const r = await this.request<{
      total_sessions: number;
      total_chunks: number;
      sessions_by_tool: Record<string, number>;
      sessions_by_category: Record<string, number>;
    }>("GET", "/v1/stats");
    return {
      totalSessions: r.total_sessions ?? 0,
      totalChunks: r.total_chunks ?? 0,
      byTool: r.sessions_by_tool ?? {},
      byCategory: r.sessions_by_category ?? {},
    };
  }
}

// ─── factory ──────────────────────────────────────────────────────────────────

/** Create a new ApiClient from explicit credentials. */
export function makeApiClient(baseUrl: string, apiKey: string): ApiClient {
  return new ApiClient(baseUrl, apiKey);
}

/** Lazily create an ApiClient seeded from the settings store. */
export async function getApiClient(): Promise<ApiClient> {
  const { useSettings } = await import("../../store/settings");
  const { apiBaseUrl, apiKey } = useSettings.getState();
  return new ApiClient(apiBaseUrl, apiKey);
}
