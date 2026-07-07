import React, { useRef, useState, useMemo, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowDown, ArrowUp, Search, TerminalSquare } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Tabs } from "@/components/ui/Tabs";
import { Toggle } from "@/components/ui/Toggle";
import { EmptyState } from "@/components/ui/EmptyState";
import { SessionRowSkeleton } from "@/components/ui/Skeleton";
import { SessionRow } from "@/components/SessionRow";
import { WelcomeHero } from "./WelcomeHero";
import { useApp } from "@/store/app";
import { useSettings } from "@/store/settings";
import { makeApiClient } from "@/lib/api/client";
import type { SortField } from "@/store/app";
import { filterSessions, sortSessions, deriveProjects } from "@/lib/sessions-filter";
import type { Tool } from "@/lib/types";

const VIRTUALIZE_THRESHOLD = 100;

type FilterTab = "all" | Tool;

const filterTabs: { value: FilterTab; label: string }[] = [
  { value: "all", label: "All" },
  { value: "claude-code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "kilo-code", label: "Kilo Code" },
];

const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: "date", label: "Date" },
  { value: "project", label: "Project" },
  { value: "tool", label: "Tool" },
  { value: "tokens", label: "Tokens" },
  { value: "cost", label: "Cost" },
  { value: "messages", label: "Messages" },
];

const DATE_RANGE_OPTIONS = [
  { value: "all", label: "All time" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
] as const;

/** Virtualized session list rendered only when filtered.length > VIRTUALIZE_THRESHOLD. */
function VirtualSessionList({
  sessions: filteredSessions,
  pushedSet,
  reviewStatusMap,
  onSelect,
}: {
  sessions: ReturnType<typeof filterSessions>;
  pushedSet: Set<string>;
  reviewStatusMap: Map<string, "pending" | "rejected">;
  onSelect: (id: string) => void;
}) {
  const parentRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: filteredSessions.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 80,
    overscan: 5,
    measureElement: (el) => el.getBoundingClientRect().height,
  });

  return (
    <div ref={parentRef} className="flex-1 overflow-y-auto h-full">
      <div
        style={{ height: `${rowVirtualizer.getTotalSize()}px`, position: "relative" }}
      >
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const session = filteredSessions[virtualRow.index];
          return (
            <div
              key={virtualRow.key}
              data-index={virtualRow.index}
              ref={rowVirtualizer.measureElement}
              style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${virtualRow.start}px)` }}
            >
              <SessionRow
                session={session}
                pushed={pushedSet.has(session.id)}
                reviewStatus={reviewStatusMap.get(session.id)}
                onClick={() => onSelect(session.id)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function SessionsPage() {
  const navigate = useNavigate();
  const settings = useSettings();
  const { sessions, loading, pushedIds, listPrefs, setListPrefs } = useApp();
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<FilterTab>("all");
  const [project, setProject] = useState("all");
  const [graphOnly, setGraphOnly] = useState(false);

  // Ids of sessions the hub has already graphed (for the "Has graph" filter).
  const [graphIds, setGraphIds] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!settings.apiBaseUrl) return;
    let cancelled = false;
    makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "")
      .getGraphSessionIds()
      .then((ids) => {
        if (!cancelled) setGraphIds(new Set(ids));
      })
      .catch(() => {
        /* hub unreachable — leave the filter empty */
      });
    return () => {
      cancelled = true;
    };
  }, [settings.apiBaseUrl, settings.apiKey]);

  // Review status for pushed sessions still awaiting (or rejected in) review.
  const [reviewStatusMap, setReviewStatusMap] = useState<Map<string, "pending" | "rejected">>(
    new Map(),
  );
  useEffect(() => {
    if (!settings.apiBaseUrl) return;
    let cancelled = false;
    const client = makeApiClient(settings.apiBaseUrl, settings.apiKey ?? "");
    Promise.all([
      client.listReviews({ status: "pending", limit: 500 }),
      client.listReviews({ status: "rejected", limit: 500 }),
    ])
      .then(([pending, rejected]) => {
        if (cancelled) return;
        const map = new Map<string, "pending" | "rejected">();
        for (const r of rejected.items) map.set(r.sessionId, "rejected");
        for (const r of pending.items) map.set(r.sessionId, "pending");
        setReviewStatusMap(map);
      })
      .catch(() => {
        /* hub unreachable or reviews disabled — leave the map empty */
      });
    return () => {
      cancelled = true;
    };
  }, [settings.apiBaseUrl, settings.apiKey]);

  const pushedSet = useMemo((): Set<string> => {
    if (pushedIds instanceof Set) return pushedIds as Set<string>;
    if (Array.isArray(pushedIds)) return new Set(pushedIds as string[]);
    return new Set();
  }, [pushedIds]);

  // Derive unique project list for the dropdown
  const projects = useMemo(() => deriveProjects(sessions), [sessions]);

  const projectOptions = useMemo(() => [
    { value: "all", label: "All projects" },
    ...projects.map((p) => ({ value: p, label: p })),
  ], [projects]);

  const filtered = useMemo(() => {
    let f = filterSessions(sessions, {
      tab,
      search,
      project,
      prefs: { dateRange: listPrefs.dateRange, compactedOnly: listPrefs.compactedOnly },
    });
    if (graphOnly) f = f.filter((s) => graphIds.has(s.id));
    return sortSessions(f, { sortField: listPrefs.sortField, sortOrder: listPrefs.sortOrder });
  }, [sessions, tab, search, project, listPrefs, graphOnly, graphIds]);

  // Show welcome hero only when no sessions AND no loading
  if (!loading && sessions.length === 0) {
    return <WelcomeHero />;
  }

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="flex items-center justify-between px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <div>
          <h1 className="text-h2 font-semibold text-ink">My Sessions</h1>
          {!loading && (
            <p className="text-small text-ink-faint mt-0.5">
              {sessions.length} session{sessions.length !== 1 ? "s" : ""} found locally
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <Input
            placeholder="Search sessions…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            leading={<Search size={14} />}
            className="w-56"
          />
        </div>
      </div>

      {/* Filter toolbar */}
      <div className="px-6 py-3 border-b border-border bg-bg shrink-0 flex flex-wrap items-center gap-3">
        {/* Tool tabs */}
        <Tabs
          items={filterTabs.map((t) => ({
            ...t,
            count:
              t.value === "all"
                ? sessions.length
                : sessions.filter((s) => s.tool === t.value).length,
          }))}
          value={tab}
          onChange={setTab}
        />

        {/* Spacer */}
        <div className="flex-1" />

        {/* Project dropdown */}
        {projects.length > 0 && (
          <div className="w-40">
            <Select
              options={projectOptions}
              value={project}
              onChange={(e) => setProject(e.target.value)}
              aria-label="Filter by project"
            />
          </div>
        )}

        {/* Date range dropdown */}
        <div className="w-36">
          <Select
            options={[...DATE_RANGE_OPTIONS]}
            value={listPrefs.dateRange}
            onChange={(e) =>
              setListPrefs({ dateRange: e.target.value as typeof listPrefs.dateRange })
            }
            aria-label="Filter by date range"
          />
        </div>

        {/* Sort field + direction */}
        <div className="flex items-center gap-1">
          <div className="w-32">
            <Select
              options={SORT_OPTIONS}
              value={listPrefs.sortField}
              onChange={(e) => setListPrefs({ sortField: e.target.value as SortField })}
              aria-label="Sort by"
            />
          </div>
          <button
            type="button"
            onClick={() =>
              setListPrefs({ sortOrder: listPrefs.sortOrder === "asc" ? "desc" : "asc" })
            }
            className="h-9 w-9 flex items-center justify-center rounded-[8px] border border-border bg-bg-elevated text-ink-faint hover:text-ink hover:border-border-strong transition-colors duration-150 focus-ring"
            aria-label={listPrefs.sortOrder === "asc" ? "Sort ascending" : "Sort descending"}
          >
            {listPrefs.sortOrder === "asc" ? <ArrowUp size={14} /> : <ArrowDown size={14} />}
          </button>
        </div>

        {/* Has-graph toggle */}
        {graphIds.size > 0 && (
          <Toggle
            checked={graphOnly}
            onChange={setGraphOnly}
            label={`Has graph (${graphIds.size})`}
          />
        )}

        {/* Compacted only toggle */}
        <Toggle
          checked={listPrefs.compactedOnly}
          onChange={(v) => setListPrefs({ compactedOnly: v })}
          label="Compacted only"
        />
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto flex flex-col min-h-0">
        {loading ? (
          <div>
            {Array.from({ length: 6 }).map((_, i) => (
              <SessionRowSkeleton key={i} />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={<TerminalSquare size={40} strokeWidth={1.25} />}
            headline="No sessions match your search"
            body="Try a different keyword or tool filter to find your sessions."
            cta={
              <button
                className="text-small text-accent hover:text-accent-ink transition-colors duration-120"
                onClick={() => {
                  setSearch("");
                  setTab("all");
                  setProject("all");
                  setListPrefs({ dateRange: "all", compactedOnly: false });
                }}
              >
                Clear filters
              </button>
            }
          />
        ) : filtered.length > VIRTUALIZE_THRESHOLD ? (
          <VirtualSessionList
            sessions={filtered}
            pushedSet={pushedSet}
            reviewStatusMap={reviewStatusMap}
            onSelect={(id) => navigate(`/sessions/${id}`)}
          />
        ) : (
          <div>
            {filtered.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                pushed={pushedSet.has(session.id)}
                reviewStatus={reviewStatusMap.get(session.id)}
                onClick={() => navigate(`/sessions/${session.id}`)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
