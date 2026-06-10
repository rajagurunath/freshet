import React, { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Search, TerminalSquare } from "lucide-react";
import { Input } from "@/components/ui/Input";
import { Tabs } from "@/components/ui/Tabs";
import { EmptyState } from "@/components/ui/EmptyState";
import { SessionRowSkeleton } from "@/components/ui/Skeleton";
import { SessionRow } from "@/components/SessionRow";
import { WelcomeHero } from "./WelcomeHero";
import { useApp } from "@/store/app";
import type { Tool } from "@/lib/types";

type FilterTab = "all" | Tool;

const filterTabs: { value: FilterTab; label: string }[] = [
  { value: "all", label: "All" },
  { value: "claude-code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "kilo-code", label: "Kilo Code" },
];

export function SessionsPage() {
  const navigate = useNavigate();
  const { sessions, loading, pushedIds } = useApp();
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<FilterTab>("all");

  const pushedSet = useMemo((): Set<string> => {
    if (pushedIds instanceof Set) return pushedIds as Set<string>;
    if (Array.isArray(pushedIds)) return new Set(pushedIds as string[]);
    return new Set();
  }, [pushedIds]);

  const filtered = useMemo(() => {
    return sessions.filter((s) => {
      const matchesTab = tab === "all" || s.tool === tab;
      const q = search.toLowerCase();
      const matchesSearch =
        !q ||
        s.title.toLowerCase().includes(q) ||
        s.preview.toLowerCase().includes(q) ||
        (s.project?.toLowerCase().includes(q) ?? false);
      return matchesTab && matchesSearch;
    });
  }, [sessions, tab, search]);

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

      {/* Filter tabs */}
      <div className="px-6 py-3 border-b border-border bg-bg shrink-0">
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
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto">
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
                }}
              >
                Clear filters
              </button>
            }
          />
        ) : (
          <div>
            {filtered.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                pushed={pushedSet.has(session.id)}
                onClick={() => navigate(`/sessions/${session.id}`)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
