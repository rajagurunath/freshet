import React, { useState, useEffect, useCallback } from "react";
import { CheckCircle, XCircle, RefreshCw, ShieldCheck, Activity } from "lucide-react";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/Toggle";
import { Select } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/Toast";
import { useSettings } from "@/store/settings";
import { useApp } from "@/store/app";
import { makeApiClient, type ProviderInfo } from "@/lib/api/client";
import type { Category, Visibility, Tool } from "@/lib/types";
import { CATEGORIES } from "@/lib/types";
import { cn } from "@/components/ui/cn";

const categoryOptions = CATEGORIES.map((c) => ({
  value: c,
  label: c.charAt(0).toUpperCase() + c.slice(1),
}));

const visibilityOptions: { value: Visibility; label: string }[] = [
  { value: "company", label: "Company" },
  { value: "team", label: "Team" },
  { value: "private", label: "Private" },
];

type HealthStatus = "idle" | "loading" | "ok" | "error";

const autoSyncToolList: { value: Tool; label: string }[] = [
  { value: "claude-code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "kilo-code", label: "Kilo Code" },
];

const AI_PROVIDERS: { id: string; label: string; hint: string; defaultModel: string }[] = [
  {
    id: "claude-cli",
    label: "Claude (local CLI)",
    hint: "Uses your installed Claude Code — no API key. Runs on your machine.",
    defaultModel: "sonnet",
  },
  {
    id: "codex-cli",
    label: "Codex (local CLI)",
    hint: "Uses your installed Codex CLI — no API key. Runs on your machine.",
    defaultModel: "",
  },
  {
    id: "anthropic",
    label: "Anthropic API",
    hint: "Claude via api.anthropic.com (key configured on the server).",
    defaultModel: "claude-sonnet-4-6",
  },
  {
    id: "openai",
    label: "OpenAI-compatible",
    hint: "OpenRouter, Ollama, vLLM, LM Studio, or OpenAI (configured on the server).",
    defaultModel: "",
  },
];

function needsServerKey(provider: string): boolean {
  return provider === "anthropic" || provider === "openai";
}

function modelHint(provider: string): string {
  switch (provider) {
    case "claude-cli":
      return "e.g. sonnet, opus, haiku — passed to `claude --model`.";
    case "codex-cli":
      return "Leave blank to use Codex's configured default model.";
    case "anthropic":
      return "e.g. claude-sonnet-4-6.";
    case "openai":
      return "e.g. gpt-4o-mini, or anthropic/claude-sonnet-4.6 on OpenRouter.";
    default:
      return "";
  }
}

function ProviderBadge({ info, loading }: { info?: ProviderInfo; loading: boolean }) {
  if (loading) {
    return <span className="text-micro text-ink-faint">checking…</span>;
  }
  if (!info) {
    return <span className="text-micro text-ink-faint">—</span>;
  }
  if (info.available) {
    return (
      <span className="flex items-center gap-1 text-micro text-success font-medium">
        <CheckCircle size={11} /> Detected
      </span>
    );
  }
  return (
    <span className="text-micro text-warn font-medium">
      {info.needsKey ? "Needs key on server" : "Not available"}
    </span>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-h3 font-semibold text-ink">{children}</h2>
  );
}

function SectionDivider() {
  return <div className="h-px bg-border" />;
}

export function SettingsPage() {
  const settings = useSettings();
  const { syncState } = useApp();
  const { success } = useToast();

  // local state for controlled inputs
  const [apiBaseUrl, setApiBaseUrlLocal] = useState(settings.apiBaseUrl ?? "");
  const [apiKey, setApiKeyLocal] = useState(settings.apiKey ?? "");
  const [authorName, setAuthorName] = useState(settings.author?.name ?? "");
  const [authorEmail, setAuthorEmail] = useState(settings.author?.email ?? "");

  const [healthStatus, setHealthStatus] = useState<HealthStatus>("idle");

  // AI providers detected on the server
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providersLoading, setProvidersLoading] = useState(false);

  const loadProviders = useCallback(async () => {
    if (!settings.apiBaseUrl) return;
    setProvidersLoading(true);
    try {
      const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
      const res = await client.getProviders();
      setProviders(res.providers);
    } catch {
      setProviders([]);
    } finally {
      setProvidersLoading(false);
    }
  }, [settings.apiBaseUrl, settings.apiKey]);

  useEffect(() => {
    loadProviders();
  }, [loadProviders]);

  const testConnection = async () => {
    setHealthStatus("loading");
    try {
      const client = makeApiClient(apiBaseUrl, apiKey);
      await client.health();
      setHealthStatus("ok");
    } catch {
      setHealthStatus("error");
    }
  };

  const saveConnection = () => {
    settings.setApiBaseUrl?.(apiBaseUrl);
    settings.setApiKey?.(apiKey);
    // Fallback: use generic update if individual setters don't exist
    settings.update?.({ apiBaseUrl, apiKey });
    success("Connection settings saved.");
  };

  const saveIdentity = () => {
    const update = {
      author: {
        id: settings.author?.id ?? authorEmail,
        email: authorEmail,
        name: authorName,
      },
    };
    settings.setAuthor?.(update.author);
    settings.update?.(update);
    success("Identity saved.");
  };

  const isAutoSync = settings.syncMode === "auto";

  const toggleAutoSyncTool = (tool: Tool) => {
    const current: Tool[] = settings.autoSyncTools ?? [];
    const next = current.includes(tool)
      ? current.filter((t) => t !== tool)
      : [...current, tool];
    settings.setAutoSyncTools?.(next);
    settings.update?.({ autoSyncTools: next });
  };

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <h1 className="text-h2 font-semibold text-ink">Settings</h1>
        <p className="text-small text-ink-faint mt-0.5">Manage your connection, identity, and sync preferences</p>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-[600px] mx-auto px-6 py-8 space-y-10">

          {/* Connection */}
          <section className="space-y-5">
            <SectionHeading>Connection</SectionHeading>
            <Input
              label="Hub API base URL"
              type="url"
              placeholder="https://hub.yourcompany.com"
              value={apiBaseUrl}
              onChange={(e) => setApiBaseUrlLocal(e.target.value)}
              hint="The base URL of your Context Hub API server."
            />
            <Input
              label="API key"
              type="password"
              placeholder="sk-…"
              value={apiKey}
              onChange={(e) => setApiKeyLocal(e.target.value)}
              hint="Your personal API key for the hub."
            />
            <div className="flex items-center gap-3">
              <Button variant="secondary" size="sm" onClick={saveConnection}>
                Save
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={healthStatus === "loading"}
                onClick={testConnection}
              >
                <RefreshCw size={13} />
                Test connection
              </Button>
              {healthStatus === "ok" && (
                <span className="flex items-center gap-1.5 text-small text-success">
                  <CheckCircle size={14} />
                  Connected
                </span>
              )}
              {healthStatus === "error" && (
                <span className="flex items-center gap-1.5 text-small text-danger">
                  <XCircle size={14} />
                  Could not connect
                </span>
              )}
            </div>
          </section>

          <SectionDivider />

          {/* AI Provider */}
          <section className="space-y-5">
            <div>
              <SectionHeading>AI Provider</SectionHeading>
              <p className="text-small text-ink-faint mt-1">
                Choose which model Context Hub uses to summarize sessions and answer questions.
                By default it uses your own installed coding agent — no API key required.
              </p>
            </div>

            <div className="space-y-2">
              {AI_PROVIDERS.map((p) => {
                const info = providers.find((x) => x.id === p.id);
                const selected = (settings.llmProvider ?? "claude-cli") === p.id;
                return (
                  <button
                    key={p.id}
                    onClick={() => settings.update?.({ llmProvider: p.id, llmModel: p.defaultModel })}
                    className={cn(
                      "w-full text-left flex items-start gap-3 p-3 rounded-[8px] border transition-all duration-120",
                      selected
                        ? "border-accent bg-accent-wash"
                        : "border-border bg-bg-elevated hover:border-border-strong",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center",
                        selected ? "border-accent" : "border-border-strong",
                      )}
                    >
                      {selected && <span className="w-2 h-2 rounded-full bg-accent" />}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-small font-medium text-ink">{p.label}</span>
                        <ProviderBadge info={info} loading={providersLoading} />
                      </div>
                      <p className="text-micro text-ink-faint mt-0.5">{p.hint}</p>
                    </div>
                  </button>
                );
              })}
            </div>

            <Input
              label="Model"
              type="text"
              placeholder="sonnet"
              value={settings.llmModel ?? ""}
              onChange={(e) => settings.update?.({ llmModel: e.target.value })}
              hint={modelHint(settings.llmProvider ?? "claude-cli")}
            />

            {needsServerKey(settings.llmProvider ?? "claude-cli") && (
              <p className="text-small text-ink-faint">
                API keys for hosted providers are configured securely on the server
                (<code className="font-mono text-micro">apps/api/.env</code>), never in the desktop app.
              </p>
            )}

            {/* Consent status */}
            <div className="flex items-start gap-3 p-3 rounded-[8px] bg-bg-sunken border border-border">
              <ShieldCheck
                size={16}
                className={cn("shrink-0 mt-0.5", settings.aiConsent ? "text-success" : "text-ink-faint")}
              />
              <div className="flex-1">
                <p className="text-small text-ink">
                  {settings.aiConsent
                    ? "You've allowed Context Hub to use your AI provider."
                    : "You'll be asked for permission the first time you use AI."}
                </p>
                {settings.aiConsent && (
                  <button
                    onClick={() => {
                      settings.update?.({ aiConsent: false });
                      success("AI consent revoked. You'll be asked again next time.");
                    }}
                    className="text-small text-accent hover:text-accent-ink transition-colors duration-120 mt-1"
                  >
                    Revoke consent
                  </button>
                )}
              </div>
            </div>

            <Button variant="ghost" size="sm" loading={providersLoading} onClick={loadProviders}>
              <RefreshCw size={13} />
              Re-detect providers
            </Button>
          </section>

          <SectionDivider />

          {/* Identity */}
          <section className="space-y-5">
            <SectionHeading>Identity</SectionHeading>
            <Input
              label="Display name"
              type="text"
              placeholder="Your name"
              value={authorName}
              onChange={(e) => setAuthorName(e.target.value)}
            />
            <Input
              label="Email"
              type="email"
              placeholder="you@company.com"
              value={authorEmail}
              onChange={(e) => setAuthorEmail(e.target.value)}
            />
            <Button variant="secondary" size="sm" onClick={saveIdentity}>
              Save identity
            </Button>
          </section>

          <SectionDivider />

          {/* Sync mode */}
          <section className="space-y-5">
            <div>
              <SectionHeading>Sync</SectionHeading>
              <p className="text-small text-ink-faint mt-1">
                Control how sessions get pushed to the hub.
              </p>
            </div>

            {/* Segmented control */}
            <div className="flex gap-0.5 bg-bg-sunken p-0.5 rounded-[8px] w-fit">
              {(["manual", "auto"] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => {
                    settings.setSyncMode?.(mode);
                    settings.update?.({ syncMode: mode });
                  }}
                  className={cn(
                    "h-7 px-4 rounded-[6px] text-small font-medium transition-all duration-120",
                    settings.syncMode === mode
                      ? "bg-bg-elevated text-ink border border-border"
                      : "text-ink-soft hover:text-ink",
                  )}
                >
                  {mode.charAt(0).toUpperCase() + mode.slice(1)}
                </button>
              ))}
            </div>

            {isAutoSync && (
              <div className="space-y-3 p-4 bg-bg-sunken rounded-card border border-border">
                <p className="text-small font-medium text-ink">
                  Auto-push for these tools:
                </p>
                <div className="space-y-2">
                  {autoSyncToolList.map(({ value, label }) => (
                    <Toggle
                      key={value}
                      checked={(settings.autoSyncTools ?? []).includes(value)}
                      onChange={() => toggleAutoSyncTool(value)}
                      label={label}
                    />
                  ))}
                </div>
                <p className="text-small text-ink-faint mt-2">
                  Sessions from selected tools will be automatically pushed using your default category and visibility settings.
                </p>
              </div>
            )}

            {/* Sync status row */}
            <div className="flex items-start gap-3 p-3 rounded-[8px] bg-bg-sunken border border-border">
              <Activity
                size={16}
                className={cn(
                  "shrink-0 mt-0.5",
                  isAutoSync ? "text-accent" : "text-ink-faint",
                )}
              />
              <div className="flex-1 min-w-0 space-y-0.5">
                <p className="text-small font-medium text-ink">Auto-sync status</p>
                <p className="text-micro text-ink-faint">
                  {syncState.lastRunAt
                    ? `Last run: ${new Date(syncState.lastRunAt).toLocaleString()}`
                    : "Not yet run this session."}
                </p>
                {syncState.queueLength > 0 && (
                  <p className="text-micro text-ink-soft">
                    {syncState.queueLength} file{syncState.queueLength !== 1 ? "s" : ""} queued
                  </p>
                )}
                {Object.keys(syncState.syncErrors).length > 0 && (
                  <details className="mt-1">
                    <summary className="text-micro text-warn cursor-pointer">
                      {Object.keys(syncState.syncErrors).length} error{Object.keys(syncState.syncErrors).length !== 1 ? "s" : ""}
                    </summary>
                    <ul className="mt-1 space-y-0.5">
                      {Object.entries(syncState.syncErrors).map(([fp, err]) => (
                        <li key={fp} className="text-micro text-ink-faint truncate">
                          <span className="font-mono">{fp.split("/").pop()}</span>: {err}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            </div>
          </section>

          <SectionDivider />

          {/* Privacy */}
          <section className="space-y-5">
            <div>
              <SectionHeading>Privacy</SectionHeading>
              <p className="text-small text-ink-faint mt-1">
                Default behavior when pushing sessions.
              </p>
            </div>

            <Toggle
              checked={settings.redactBeforePush ?? false}
              onChange={(v) => {
                settings.setRedactBeforePush?.(v);
                settings.update?.({ redactBeforePush: v });
              }}
              label="Redact secrets before push"
              description="Automatically scrub API keys, tokens, and passwords from sessions."
            />

            <Select
              label="Default category"
              options={categoryOptions}
              value={settings.defaultCategory ?? "engineering"}
              onChange={(e) => {
                const cat = e.target.value as Category;
                settings.setDefaultCategory?.(cat);
                settings.update?.({ defaultCategory: cat });
              }}
            />

            <Select
              label="Default visibility"
              options={visibilityOptions}
              value={settings.defaultVisibility ?? "company"}
              onChange={(e) => {
                const vis = e.target.value as Visibility;
                settings.setDefaultVisibility?.(vis);
                settings.update?.({ defaultVisibility: vis });
              }}
            />
          </section>

          {/* Footer spacing */}
          <div className="pb-8" />
        </div>
      </div>
    </div>
  );
}
