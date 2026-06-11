import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Globe, BarChart2, User, Link } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatTile } from "@/components/StatTile";
import { ToolChip } from "@/components/ToolChip";
import { CategoryChip } from "@/components/CategoryChip";
import { useToast } from "@/components/ui/Toast";
import { useSettings } from "@/store/settings";
import { makeApiClient } from "@/lib/api/client";
import type { Category, Tool } from "@/lib/types";
import { CATEGORIES } from "@/lib/types";
import { cn } from "@/components/ui/cn";

function relativeTime(iso?: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

interface HubSession {
  id: string;
  title: string;
  tool: Tool;
  category: Category;
  author?: string;
  preview: string;
  startedAt?: string;
}

interface StatsData {
  totalSessions?: number;
  totalChunks?: number;
  byCategory?: Partial<Record<Category, number>>;
}

export function HubPage() {
  const navigate = useNavigate();
  const settings = useSettings();
  const { success, error: toastError } = useToast();

  const [stats, setStats] = useState<StatsData | null>(null);
  const [sessions, setSessions] = useState<HubSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<Category | "all">("all");
  const [search, setSearch] = useState("");

  const copyShareLink = useCallback(
    async (e: React.MouseEvent, sessionId: string) => {
      e.stopPropagation();
      try {
        const client = makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "");
        const { url } = await client.shareSession(sessionId);
        await navigator.clipboard.writeText(url);
        success("Share link copied — paste it into your PR");
      } catch {
        toastError("Failed to generate share link.");
      }
    },
    [settings.apiBaseUrl, settings.apiKey, success, toastError],
  );

  const load = useCallback(async () => {
    if (!settings.apiBaseUrl) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
      const [statsData, hubSessions] = await Promise.all([
        client.stats(),
        client.listHubSessions(
          categoryFilter !== "all" ? { category: categoryFilter } : undefined,
        ),
      ]);
      setStats(statsData as StatsData);
      setSessions(hubSessions as HubSession[]);
    } catch {
      setError("Could not reach the hub. Check your connection settings.");
      toastError("Failed to load hub data.");
    } finally {
      setLoading(false);
    }
  }, [settings.apiBaseUrl, settings.apiKey, categoryFilter, toastError]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = sessions.filter((s) => {
    const q = search.toLowerCase();
    return (
      !q ||
      s.title.toLowerCase().includes(q) ||
      s.preview.toLowerCase().includes(q) ||
      (s.author?.toLowerCase().includes(q) ?? false)
    );
  });

  const hasApi = Boolean(settings.apiBaseUrl);

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center justify-between px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <div>
          <h1 className="text-h2 font-semibold text-ink">Company Hub</h1>
          <p className="text-small text-ink-faint mt-0.5">
            Shared sessions and institutional knowledge
          </p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {!hasApi ? (
          <EmptyState
            icon={<Globe size={40} strokeWidth={1.25} />}
            headline="Connect a hub first"
            body="Add your Context Hub API URL and key in Settings to see your team's shared sessions."
            cta={
              <button
                className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                onClick={() => navigate("/settings")}
              >
                Go to Settings
              </button>
            }
          />
        ) : error ? (
          <EmptyState
            icon={<Globe size={40} strokeWidth={1.25} />}
            headline="Could not reach the hub"
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
        ) : (
          <>
            {/* Stats row */}
            {loading ? (
              <div className="grid grid-cols-3 gap-4">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-24 rounded-card" />
                ))}
              </div>
            ) : stats ? (
              <div className="grid grid-cols-3 gap-4">
                <StatTile
                  label="Total Sessions"
                  value={stats.totalSessions ?? 0}
                  icon={<BarChart2 size={16} />}
                />
                <StatTile
                  label="Total Chunks"
                  value={stats.totalChunks ?? 0}
                  icon={<BarChart2 size={16} />}
                />
                <Card className="p-5">
                  <p className="text-small text-ink-soft font-medium mb-3">By Category</p>
                  <div className="flex flex-wrap gap-1.5">
                    {CATEGORIES.map((cat) => {
                      const count = stats.byCategory?.[cat] ?? 0;
                      return count > 0 ? (
                        <CategoryChip key={cat} category={cat} />
                      ) : null;
                    })}
                  </div>
                </Card>
              </div>
            ) : null}

            {/* Category filters */}
            <div className="flex items-center gap-2 flex-wrap">
              <button
                onClick={() => setCategoryFilter("all")}
                className={cn(
                  "px-3 py-1 rounded-full text-small border transition-colors duration-120",
                  categoryFilter === "all"
                    ? "bg-accent text-white border-accent"
                    : "bg-bg-elevated border-border text-ink-soft hover:border-border-strong",
                )}
              >
                All
              </button>
              {CATEGORIES.map((cat) => (
                <button
                  key={cat}
                  onClick={() => setCategoryFilter(cat)}
                  className={cn(
                    "px-3 py-1 rounded-full text-small border transition-colors duration-120",
                    categoryFilter === cat
                      ? "bg-accent text-white border-accent"
                      : "bg-bg-elevated border-border text-ink-soft hover:border-border-strong",
                  )}
                >
                  {cat.charAt(0).toUpperCase() + cat.slice(1)}
                </button>
              ))}
            </div>

            {/* Search */}
            <div className="relative max-w-sm">
              <input
                type="text"
                placeholder="Search hub sessions…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full h-9 pl-3 pr-3 bg-bg-elevated border border-border rounded-[8px] text-body text-ink placeholder:text-ink-faint focus-ring hover:border-border-strong transition-colors duration-150"
              />
            </div>

            {/* Sessions grid */}
            {loading ? (
              <div className="grid grid-cols-2 gap-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-32 rounded-card" />
                ))}
              </div>
            ) : filtered.length === 0 ? (
              <EmptyState
                icon={<Globe size={40} strokeWidth={1.25} />}
                headline="No sessions here yet"
                body="Push your first curated session from My Sessions to start building your team's knowledge base."
              />
            ) : (
              <div className="grid grid-cols-2 gap-4">
                {filtered.map((s) => (
                  <Card
                    key={s.id}
                    hoverable
                    className="p-4 flex flex-col gap-3"
                    onClick={() => {
                      // Navigate to local session if it exists, otherwise open detail view
                      navigate(`/sessions/${s.id}`);
                    }}
                  >
                    <div className="flex items-center gap-2 flex-wrap">
                      <ToolChip tool={s.tool} />
                      <CategoryChip category={s.category} />
                    </div>
                    <div>
                      <h3 className="text-body font-semibold text-ink line-clamp-1">
                        {s.title}
                      </h3>
                      <p className="mt-1 text-small text-ink-faint line-clamp-2 leading-relaxed">
                        {s.preview}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 text-micro text-ink-faint mt-auto">
                      {s.author && (
                        <>
                          <User size={11} />
                          <span>{s.author}</span>
                          <span className="text-border-strong">·</span>
                        </>
                      )}
                      <span>{relativeTime(s.startedAt)}</span>
                      <button
                        onClick={(e) => void copyShareLink(e, s.id)}
                        className="ml-auto flex items-center gap-1 text-ink-faint hover:text-accent transition-colors duration-120"
                        title="Copy share link"
                        aria-label="Copy share link"
                      >
                        <Link size={11} />
                        Copy link
                      </button>
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
