import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ListChecks,
  Check,
  X,
  Copy,
  Pickaxe,
  RefreshCw,
  ExternalLink,
  CheckCircle2,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";
import { cn } from "@/components/ui/cn";
import { useApp } from "@/store/app";
import { useSettings } from "@/store/settings";
import { makeApiClient } from "@/lib/api/client";
import { groupRulesByStatus, rulesToMarkdown, type Rule } from "@/lib/rules";

export function RulesPage() {
  const navigate = useNavigate();
  const settings = useSettings();
  const localSessions = useApp((s) => s.sessions);
  const { success: toastSuccess, error: toastError } = useToast();

  const [rules, setRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mining, setMining] = useState(false);
  const [acting, setActing] = useState<string | null>(null);

  const hasApi = Boolean(settings.apiBaseUrl);

  const client = useCallback(
    () => makeApiClient(settings.apiBaseUrl, settings.apiKey ?? ""),
    [settings.apiBaseUrl, settings.apiKey],
  );

  const load = useCallback(async () => {
    if (!settings.apiBaseUrl) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const page = await client().listRules({ limit: 500 });
      setRules(page.items);
    } catch {
      setError("Could not load rules. Check your connection settings.");
    } finally {
      setLoading(false);
    }
  }, [settings.apiBaseUrl, client]);

  useEffect(() => {
    load();
  }, [load]);

  const groups = useMemo(() => groupRulesByStatus(rules), [rules]);
  const localSessionIds = useMemo(
    () => new Set(localSessions.map((s) => s.id)),
    [localSessions],
  );

  const setStatus = async (rule: Rule, action: "accept" | "reject") => {
    setActing(rule.id);
    try {
      const updated =
        action === "accept"
          ? await client().acceptRule(rule.id)
          : await client().rejectRule(rule.id);
      setRules((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      toastSuccess(action === "accept" ? "Rule accepted." : "Rule rejected.");
    } catch (e) {
      toastError(e instanceof Error ? e.message : `Could not ${action} the rule.`);
    } finally {
      setActing(null);
    }
  };

  const mineNow = async () => {
    setMining(true);
    try {
      await client().mineRules({ nSessions: 20 });
      toastSuccess("Mining started — refresh in a minute for new proposals.");
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Could not start rule mining.");
    } finally {
      setMining(false);
    }
  };

  const copyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(rulesToMarkdown(groups.accepted));
      toastSuccess("Copied — paste into your CLAUDE.md.");
    } catch {
      toastError("Could not copy to clipboard.");
    }
  };

  const isEmpty = !loading && !error && rules.length === 0;

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center justify-between gap-4 px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <div>
          <h1 className="text-h2 font-semibold text-ink">Rules</h1>
          <p className="text-small text-ink-faint mt-0.5">
            Recurring preferences mined from your sessions — nothing is exported until you accept it
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={load}
            aria-label="Refresh rules"
            className="h-9 w-9 flex items-center justify-center rounded-[8px] border border-border text-ink-soft hover:bg-bg-sunken hover:text-ink transition-colors duration-120"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          </button>
          <Button variant="primary" onClick={mineNow} loading={mining} disabled={!hasApi}>
            <Pickaxe size={14} />
            Mine rules now
          </Button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {!hasApi ? (
          <EmptyState
            icon={<ListChecks size={40} strokeWidth={1.25} />}
            headline="Connect a hub first"
            body="Add your Context Hub API URL and key in Settings — the hub mines rules from your pushed sessions."
            cta={
              <button
                className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                onClick={() => navigate("/settings")}
              >
                Go to Settings
              </button>
            }
          />
        ) : loading ? (
          <div className="px-6 py-6 space-y-3 max-w-[860px]">
            <Skeleton className="h-24 w-full rounded-card" />
            <Skeleton className="h-24 w-full rounded-card" />
            <Skeleton className="h-24 w-full rounded-card" />
          </div>
        ) : error ? (
          <EmptyState
            icon={<ListChecks size={40} strokeWidth={1.25} />}
            headline="Could not load rules"
            body={error}
            cta={
              <button
                className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                onClick={load}
              >
                Retry
              </button>
            }
          />
        ) : isEmpty ? (
          <EmptyState
            icon={<ListChecks size={40} strokeWidth={1.25} />}
            headline="No rules mined yet"
            body='Push a few sessions, then hit "Mine rules now" — the hub looks for recurring preferences like commit style, naming and tooling choices.'
            cta={
              <Button variant="primary" onClick={mineNow} loading={mining}>
                <Pickaxe size={14} />
                Mine rules now
              </Button>
            }
          />
        ) : (
          <div className="px-6 py-6 space-y-8 max-w-[860px]">
            {/* Proposed */}
            <section>
              <div className="flex items-center gap-2 mb-3">
                <h2 className="text-h3 font-semibold text-ink">Proposed</h2>
                <Badge color="accent">{groups.proposed.length}</Badge>
              </div>
              {groups.proposed.length === 0 ? (
                <p className="text-small text-ink-faint">
                  Nothing awaiting review. Mine again after pushing more sessions.
                </p>
              ) : (
                <ul className="space-y-3">
                  {groups.proposed.map((rule) => (
                    <li
                      key={rule.id}
                      className="rounded-card border border-border bg-bg-elevated px-4 py-3.5"
                    >
                      <p className="text-body text-ink leading-relaxed">{rule.text}</p>
                      {rule.rationale && (
                        <p className="text-small text-ink-faint italic mt-1">{rule.rationale}</p>
                      )}
                      <div className="flex items-end justify-between gap-3 mt-3">
                        {/* Evidence */}
                        <div className="min-w-0">
                          {rule.evidence.length > 0 && (
                            <>
                              <span className="block text-micro font-semibold uppercase tracking-wide text-ink-faint mb-1">
                                Evidence
                              </span>
                              <div className="flex flex-wrap gap-x-3 gap-y-1">
                                {rule.evidence.map((sid) =>
                                  localSessionIds.has(sid) ? (
                                    <button
                                      key={sid}
                                      onClick={() => navigate(`/sessions/${sid}`)}
                                      className="flex items-center gap-1 text-micro font-mono text-accent hover:text-accent-ink transition-colors duration-120 max-w-[220px]"
                                    >
                                      <span className="truncate">{sid}</span>
                                      <ExternalLink size={10} className="shrink-0" />
                                    </button>
                                  ) : (
                                    <span
                                      key={sid}
                                      className="text-micro font-mono text-ink-faint truncate max-w-[220px]"
                                    >
                                      {sid}
                                    </span>
                                  ),
                                )}
                              </div>
                            </>
                          )}
                        </div>
                        {/* Actions */}
                        <div className="flex items-center gap-2 shrink-0">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setStatus(rule, "reject")}
                            loading={acting === rule.id}
                          >
                            <X size={13} />
                            Reject
                          </Button>
                          <Button
                            size="sm"
                            variant="primary"
                            onClick={() => setStatus(rule, "accept")}
                            loading={acting === rule.id}
                          >
                            <Check size={13} />
                            Accept
                          </Button>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Accepted */}
            <section>
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="flex items-center gap-2">
                  <h2 className="text-h3 font-semibold text-ink">Accepted</h2>
                  <Badge color="success">{groups.accepted.length}</Badge>
                </div>
                {groups.accepted.length > 0 && (
                  <Button size="sm" variant="secondary" onClick={copyMarkdown}>
                    <Copy size={13} />
                    Copy as CLAUDE.md
                  </Button>
                )}
              </div>
              {groups.accepted.length === 0 ? (
                <p className="text-small text-ink-faint">
                  No accepted rules yet — accept proposals above to build your export.
                </p>
              ) : (
                <ul className="rounded-card border border-border bg-bg-elevated divide-y divide-border">
                  {groups.accepted.map((rule) => (
                    <li key={rule.id} className="flex items-start gap-2.5 px-4 py-3">
                      <CheckCircle2 size={15} className="text-success shrink-0 mt-0.5" />
                      <div className="min-w-0">
                        <p className="text-body text-ink leading-relaxed">{rule.text}</p>
                        {rule.rationale && (
                          <p className="text-small text-ink-faint italic mt-0.5">
                            {rule.rationale}
                          </p>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Rejected (collapsed count only — keeps the page focused) */}
            {groups.rejected.length > 0 && (
              <p className="text-micro text-ink-faint">
                {groups.rejected.length} rejected rule
                {groups.rejected.length === 1 ? "" : "s"} hidden — they will not be re-proposed.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
