import React, { useRef, useState, useEffect, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Cpu,
  Calendar,
  Zap,
  CheckCircle2,
  RotateCcw,
  MessagesSquare,
  User,
  Sparkles,
  Wrench,
  Clock,
  FileText,
  AlignLeft,
  GitBranch,
  Copy,
  Check,
  Link,
} from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Modal } from "@/components/ui/Modal";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Toggle } from "@/components/ui/Toggle";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { MessageBubble } from "@/components/MessageBubble";
import { ToolChip } from "@/components/ToolChip";
import { CategoryChip } from "@/components/CategoryChip";
import { AiConsentModal } from "@/components/AiConsentModal";
import { SessionGraph } from "@/components/SessionGraph";
import { useToast } from "@/components/ui/Toast";
import { useApp } from "@/store/app";
import { useSettings } from "@/store/settings";
import type { Category, Visibility, PushEnvelope, SessionMessage } from "@/lib/types";
import { CATEGORIES } from "@/lib/types";
import { makeApiClient } from "@/lib/api/client";
import { sessionStats, formatDuration } from "@/lib/aggregate";
import type { NormalizedSession } from "@/lib/types";

function relativeTime(iso?: string): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

const categoryOptions = CATEGORIES.map((c) => ({
  value: c,
  label: c.charAt(0).toUpperCase() + c.slice(1),
}));

function KpiPill({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-[8px] bg-bg border border-border">
      <span className="text-ink-faint">{icon}</span>
      <div className="flex flex-col leading-tight">
        <span className="text-micro text-ink-faint">{label}</span>
        <span className="text-small font-semibold font-mono text-ink">
          {value}
          {sub && <span className="ml-1.5 text-micro text-ink-faint font-normal">{sub}</span>}
        </span>
      </div>
    </div>
  );
}

const visibilityOptions: { value: Visibility; label: string }[] = [
  { value: "company", label: "Company — everyone can see" },
  { value: "team", label: "Team — restricted" },
  { value: "private", label: "Private — only me" },
];

/** Derive a sorted unique list of files touched (paths from tool message text). */
function deriveTouchedFiles(messages: SessionMessage[]): string[] {
  const fileSet = new Set<string>();
  const pathRe = /(?:^|\s)((?:\/|~\/|\.\/)[^\s"'`]+\.\w{1,6})/g;
  for (const m of messages) {
    if (m.role !== "tool") continue;
    let match;
    pathRe.lastIndex = 0;
    while ((match = pathRe.exec(m.text)) !== null) {
      fileSet.add(match[1]);
    }
  }
  return [...fileSet].sort();
}

// ─── Virtualized transcript ────────────────────────────────────────────────────

function VirtualTranscript({
  messages,
  onBranch,
  branchDisabledReason,
}: {
  messages: SessionMessage[];
  onBranch?: (messageId: string) => void;
  branchDisabledReason?: string;
}) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 120,
    overscan: 5,
    measureElement: (el) => el.getBoundingClientRect().height,
  });

  return (
    <div ref={parentRef} className="flex-1 overflow-y-auto h-full">
      <div
        style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}
      >
        {virtualizer.getVirtualItems().map((vRow) => (
          <div
            key={vRow.key}
            data-index={vRow.index}
            ref={virtualizer.measureElement}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${vRow.start}px)`,
            }}
          >
            <MessageBubble
              message={messages[vRow.index]}
              onBranch={onBranch}
              branchDisabledReason={branchDisabledReason}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Summary tab content ──────────────────────────────────────────────────────

function SummaryTab({
  session,
  summary,
  stats,
  sessionId,
}: {
  session: NormalizedSession;
  summary: string;
  stats: ReturnType<typeof sessionStats> | null;
  sessionId: string;
}) {
  const totalTokens = session.tokens
    ? session.tokens.input + session.tokens.output
    : undefined;

  const touchedFiles = useMemo(
    () => deriveTouchedFiles(session.messages),
    [session.messages],
  );

  return (
    <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
      {/* Summary text */}
      {summary && (
        <div className="space-y-1">
          <span className="text-micro text-ink-faint uppercase tracking-wide">Summary</span>
          <p className="text-body text-ink leading-relaxed whitespace-pre-wrap">{summary}</p>
        </div>
      )}

      {!summary && (
        <p className="text-small text-ink-faint italic">
          No summary yet — use Curate & Push to generate one with AI or write your own.
        </p>
      )}

      {/* KPI strip */}
      {stats && (
        <div className="space-y-2">
          <span className="text-micro text-ink-faint uppercase tracking-wide">Stats</span>
          <div className="flex items-stretch gap-2 flex-wrap">
            <KpiPill icon={<MessagesSquare size={13} />} label="Messages" value={session.messageCount} />
            <KpiPill icon={<User size={13} />} label="You" value={stats.userMessages} />
            <KpiPill icon={<Sparkles size={13} />} label="Assistant" value={stats.assistantMessages} />
            <KpiPill icon={<Wrench size={13} />} label="Tool calls" value={stats.toolCalls} />
            {totalTokens !== undefined && totalTokens > 0 && (
              <KpiPill
                icon={<Zap size={13} />}
                label="Tokens"
                value={formatTokens(totalTokens)}
                sub={`${formatTokens(stats.tokensIn)}↓ ${formatTokens(stats.tokensOut)}↑`}
              />
            )}
            {stats.durationMs !== undefined && (
              <KpiPill icon={<Clock size={13} />} label="Duration" value={formatDuration(stats.durationMs)} />
            )}
          </div>
        </div>
      )}

      {/* Tool-use breakdown */}
      {stats && stats.toolsUsed.length > 0 && (
        <div className="space-y-2">
          <span className="text-micro text-ink-faint uppercase tracking-wide">Tool-use breakdown</span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {stats.toolsUsed.map((t) => (
              <span
                key={t.key}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#f1f8f8] border border-[#bfe0df] text-micro font-mono text-[#15807d]"
              >
                {t.key}
                <span className="text-ink-faint">×{t.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Files touched */}
      {touchedFiles.length > 0 && (
        <div className="space-y-2">
          <span className="text-micro text-ink-faint uppercase tracking-wide">Files touched</span>
          <ul className="space-y-0.5">
            {touchedFiles.slice(0, 20).map((f) => (
              <li key={f} className="text-small font-mono text-ink-soft truncate">
                {f}
              </li>
            ))}
            {touchedFiles.length > 20 && (
              <li className="text-small text-ink-faint">+{touchedFiles.length - 20} more…</li>
            )}
          </ul>
        </div>
      )}

      {/* This session's knowledge graph (+ its cross-session same_as links) */}
      <SessionGraph sessionId={sessionId} />
    </div>
  );
}

// ─── Main page component ──────────────────────────────────────────────────────

type DetailTab = "summary" | "transcript";

export function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { getSession, hydrateSession, pushedIds, markPushed, rescan } = useApp();
  const settings = useSettings();
  const { success, error: toastError } = useToast();

  const session = id ? getSession(id) : undefined;

  // Lazily parse the full transcript when opening a lite (metadata-only) session.
  useEffect(() => {
    if (id && session && session.messages.length === 0) {
      void hydrateSession(id);
    }
  }, [id, session, hydrateSession]);

  // ── Branch-from-turn state ──────────────────────────────────────────────
  const [branchMessageId, setBranchMessageId] = useState<string | null>(null);
  const [branching, setBranching] = useState(false);
  const [branchResult, setBranchResult] = useState<{
    newSessionId: string;
    resumeCommand: string;
  } | null>(null);
  const [resumeCopied, setResumeCopied] = useState(false);

  // Branching writes a forked JSONL; supported for Claude Code sessions only.
  const branchDisabledReason =
    session && session.tool !== "claude-code"
      ? "Claude Code only for now"
      : undefined;

  const parentSession = session?.parentSessionId
    ? getSession(session.parentSessionId)
    : undefined;

  const handleConfirmBranch = async () => {
    if (!session || !branchMessageId) return;
    setBranching(true);
    try {
      const { branchSession } = await import("@/lib/branch");
      const result = await branchSession(session, branchMessageId);
      setBranchMessageId(null);
      setBranchResult({
        newSessionId: result.newSessionId,
        resumeCommand: result.resumeCommand,
      });
      // Rescan so the new session appears in the list.
      void rescan();
    } catch (e) {
      setBranchMessageId(null);
      toastError(
        e instanceof Error ? e.message : "Branching failed. Check file permissions.",
      );
    } finally {
      setBranching(false);
    }
  };

  const copyResumeCommand = async () => {
    if (!branchResult) return;
    try {
      await navigator.clipboard.writeText(branchResult.resumeCommand);
      setResumeCopied(true);
      setTimeout(() => setResumeCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  const pushedSet = React.useMemo((): Set<string> => {
    if (pushedIds instanceof Set) return pushedIds as Set<string>;
    if (Array.isArray(pushedIds)) return new Set(pushedIds as string[]);
    return new Set();
  }, [pushedIds]);

  const stats = useMemo(() => (session ? sessionStats(session) : null), [session]);

  // Pre-fill with compactSummary when available — cheaper than calling the LLM.
  const [summary, setSummary] = useState(session?.compactSummary ?? "");
  const [summarizing, setSummarizing] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);

  // Default to "summary" tab when a summary/compactSummary exists.
  const hasSummary = Boolean(session?.compactSummary || summary);
  const [activeTab, setActiveTab] = useState<DetailTab>(
    hasSummary ? "summary" : "transcript",
  );

  const provider = settings.llmProvider ?? "claude-cli";
  const isLocalAgent = provider === "claude-cli" || provider === "codex-cli";
  const providerLabel =
    provider === "claude-cli"
      ? `Claude (local CLI · ${settings.llmModel ?? "sonnet"})`
      : provider === "codex-cli"
        ? "Codex (local CLI)"
        : provider === "anthropic"
          ? "Anthropic API"
          : "OpenAI-compatible";
  const [category, setCategory] = useState<Category>(settings.defaultCategory ?? "engineering");
  const [visibility, setVisibility] = useState<Visibility>(settings.defaultVisibility ?? "company");
  const [redact, setRedact] = useState(settings.redactBeforePush ?? false);
  const [redactCount, setRedactCount] = useState<number>(0);
  const [pushing, setPushing] = useState(false);

  const alreadyPushed = id ? pushedSet.has(id) : false;

  // Compute redaction count when toggled
  useEffect(() => {
    if (!session || !redact) {
      setRedactCount(0);
      return;
    }
    import("@/lib/redact")
      .then(({ redactSession }) => {
        const result = redactSession(session);
        setRedactCount(result.count);
      })
      .catch(() => setRedactCount(0));
  }, [session, redact]);

  const runSummarize = async () => {
    if (!session) return;
    setSummarizing(true);
    try {
      const client = makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "");
      const text = await client.summarize(session, provider, settings.llmModel);
      setSummary(text);
      // Switch to summary tab to show the result
      setActiveTab("summary");
    } catch (e) {
      toastError("Summarization failed. Check your connection and AI provider settings.");
    } finally {
      setSummarizing(false);
    }
  };

  const handleSummarize = () => {
    if (!settings.aiConsent) {
      setConsentOpen(true);
      return;
    }
    void runSummarize();
  };

  const handleConsentAccept = () => {
    settings.update?.({ aiConsent: true });
    setConsentOpen(false);
    void runSummarize();
  };

  const handleCopyShareLink = async () => {
    if (!session) return;
    try {
      const client = makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "");
      const { url } = await client.shareSession(session.id);
      await navigator.clipboard.writeText(url);
      success("Share link copied — paste it into your PR");
    } catch {
      toastError("Failed to generate share link.");
    }
  };

  const handlePush = async () => {
    if (!session) return;
    setPushing(true);
    try {
      let finalSession = session;

      if (redact) {
        const { redactSession } = await import("@/lib/redact");
        const result = redactSession(session);
        finalSession = result.session;
      }

      const client = makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "");

      const envelope: PushEnvelope = {
        session: finalSession,
        summary: summary || undefined,
        category,
        visibility,
        author: settings.author ?? { id: "", email: "", name: "Unknown" },
        redacted: redact,
      };

      await client.pushSession(envelope);
      markPushed(session.id);
      success("Session pushed to hub successfully.");
    } catch (e) {
      toastError("Push failed. Check your connection settings.");
    } finally {
      setPushing(false);
    }
  };

  if (!session) {
    return (
      <EmptyState
        icon={<Cpu size={40} strokeWidth={1.25} />}
        headline="Session not found"
        body="This session may have been removed or the ID is invalid."
        cta={
          <Button variant="ghost" size="sm" onClick={() => navigate("/sessions")}>
            Back to sessions
          </Button>
        }
      />
    );
  }

  const totalTokens =
    session.tokens ? session.tokens.input + session.tokens.output : undefined;

  return (
    <div className="flex h-full overflow-hidden">
      <AiConsentModal
        open={consentOpen}
        providerLabel={providerLabel}
        isLocalAgent={isLocalAgent}
        onAccept={handleConsentAccept}
        onClose={() => setConsentOpen(false)}
      />

      {/* Branch confirm modal */}
      <Modal
        open={branchMessageId !== null}
        onClose={() => !branching && setBranchMessageId(null)}
        title="Branch from this turn"
        description="Creates a new Claude Code session that forks this conversation up to and including the selected message. The original session is never modified."
        size="sm"
      >
        <div className="flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={branching}
            onClick={() => setBranchMessageId(null)}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={branching}
            onClick={handleConfirmBranch}
          >
            <GitBranch size={13} />
            Create branch
          </Button>
        </div>
      </Modal>

      {/* Branch success modal */}
      <Modal
        open={branchResult !== null}
        onClose={() => setBranchResult(null)}
        title="Branch created"
        description="Resume the new session in your terminal, or find it in your sessions list."
        size="sm"
      >
        {branchResult && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <span className="text-micro text-ink-faint uppercase tracking-wide">
                Resume command
              </span>
              <div className="flex items-center gap-2 px-3 py-2 rounded-[8px] bg-bg-sunken border border-border">
                <code className="flex-1 text-small font-mono text-ink truncate">
                  {branchResult.resumeCommand}
                </code>
                <button
                  onClick={copyResumeCommand}
                  className="text-ink-faint hover:text-ink transition-colors duration-120 shrink-0"
                  aria-label="Copy resume command"
                >
                  {resumeCopied ? (
                    <Check size={14} className="text-success" />
                  ) : (
                    <Copy size={14} />
                  )}
                </button>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setBranchResult(null)}
              >
                Close
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  const newId = branchResult.newSessionId;
                  setBranchResult(null);
                  navigate(`/sessions/${newId}`);
                }}
              >
                Reveal in sessions
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {/* Left: Main content area with tabs */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden border-r border-border">
        {/* Sticky header */}
        <div className="sticky top-0 z-10 bg-bg-elevated border-b border-border px-5 py-4 shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <button
              onClick={() => navigate("/sessions")}
              className="text-ink-faint hover:text-ink transition-colors duration-120 focus-ring rounded-[4px] mr-1"
            >
              <ArrowLeft size={16} />
            </button>
            <ToolChip tool={session.tool} />
            <h1 className="text-h3 font-semibold text-ink truncate">{session.title}</h1>
          </div>
          {/* Context line */}
          <div className="flex items-center gap-3 text-small text-ink-faint flex-wrap mb-3">
            {session.project && (
              <span className="text-ink-soft font-medium">{session.project}</span>
            )}
            {session.models.length > 0 && (
              <span className="flex items-center gap-1 font-mono">
                <Cpu size={12} />
                {session.models[0]}
                {session.models.length > 1 && ` +${session.models.length - 1}`}
              </span>
            )}
            {session.startedAt && (
              <span className="flex items-center gap-1">
                <Calendar size={12} />
                {relativeTime(session.startedAt)}
              </span>
            )}
            {session.parentSessionId && (
              <button
                type="button"
                onClick={() => navigate(`/sessions/${session.parentSessionId}`)}
                className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-accent-wash text-accent-ink border border-accent/20 hover:bg-accent-wash/70 transition-colors duration-120"
                title="Open the session this was branched from"
              >
                <GitBranch size={11} />
                Branched from{" "}
                <span className="font-medium truncate max-w-[160px]">
                  {parentSession?.title ?? session.parentSessionId}
                </span>
              </button>
            )}
          </div>

          {/* Tab bar */}
          <div className="flex items-center gap-1 border-b border-border -mb-4 pb-0">
            <button
              type="button"
              onClick={() => setActiveTab("summary")}
              className={[
                "flex items-center gap-1.5 px-3 py-2 text-small font-medium border-b-2 transition-colors duration-120",
                activeTab === "summary"
                  ? "border-accent text-accent-ink"
                  : "border-transparent text-ink-faint hover:text-ink",
              ].join(" ")}
            >
              <FileText size={13} />
              Summary
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("transcript")}
              className={[
                "flex items-center gap-1.5 px-3 py-2 text-small font-medium border-b-2 transition-colors duration-120",
                activeTab === "transcript"
                  ? "border-accent text-accent-ink"
                  : "border-transparent text-ink-faint hover:text-ink",
              ].join(" ")}
            >
              <AlignLeft size={13} />
              Transcript
              <span className="ml-1 text-micro text-ink-faint font-mono">
                {session.messageCount}
              </span>
            </button>
          </div>
        </div>

        {/* Tab content */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {activeTab === "summary" ? (
            <SummaryTab session={session} summary={summary} stats={stats} sessionId={session.id} />
          ) : (
            <VirtualTranscript
              messages={session.messages}
              onBranch={(mid) => setBranchMessageId(mid)}
              branchDisabledReason={branchDisabledReason}
            />
          )}
        </div>
      </div>

      {/* Right: Curate & Push panel */}
      <div className="w-[320px] shrink-0 flex flex-col overflow-y-auto bg-bg">
        <div className="px-5 py-5 space-y-5">
          <div>
            <h2 className="text-h3 font-semibold text-ink">Curate & Push</h2>
            <p className="text-small text-ink-faint mt-0.5">
              Enrich this session before sharing it with your team.
            </p>
          </div>

          <div className="h-px bg-border" />

          {/* AI Summary */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-small font-medium text-ink">Summary</span>
              <Button
                variant="ghost"
                size="sm"
                loading={summarizing}
                onClick={handleSummarize}
              >
                {summarizing ? "Summarizing…" : summary ? "Re-summarize" : "Summarize with AI"}
              </Button>
            </div>
            <Textarea
              placeholder="Write a summary or use AI to generate one…"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              className="min-h-[100px] text-small"
            />
          </div>

          {/* Category */}
          <Select
            label="Category"
            options={categoryOptions}
            value={category}
            onChange={(e) => setCategory(e.target.value as Category)}
          />

          {/* Visibility */}
          <Select
            label="Visibility"
            options={visibilityOptions}
            value={visibility}
            onChange={(e) => setVisibility(e.target.value as Visibility)}
          />

          <div className="h-px bg-border" />

          {/* Redact toggle */}
          <div className="space-y-2">
            <Toggle
              checked={redact}
              onChange={setRedact}
              label="Redact secrets before push"
              description="Automatically removes API keys, tokens, and passwords."
            />
            {redact && redactCount > 0 && (
              <p className="text-small text-warn pl-12">
                {redactCount} potential secret{redactCount !== 1 ? "s" : ""} will be redacted.
              </p>
            )}
            {redact && redactCount === 0 && (
              <p className="text-small text-success pl-12">
                No secrets detected.
              </p>
            )}
          </div>

          <div className="h-px bg-border" />

          {/* Push button */}
          {alreadyPushed ? (
            <Card className="p-4 bg-[#e8f5ef] border-[#b6dac7]">
              <div className="flex items-center gap-2 text-success">
                <CheckCircle2 size={16} />
                <span className="text-small font-medium">Pushed to hub</span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="mt-3 w-full"
                onClick={handleCopyShareLink}
              >
                <Link size={13} />
                Copy share link
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 w-full"
                loading={pushing}
                onClick={handlePush}
              >
                <RotateCcw size={13} />
                Push again
              </Button>
            </Card>
          ) : (
            <Button
              variant="primary"
              size="md"
              className="w-full"
              loading={pushing}
              onClick={handlePush}
            >
              Push to Hub
            </Button>
          )}

          {settings.apiBaseUrl && (
            <p className="text-micro text-ink-faint text-center">
              Pushing to {settings.apiBaseUrl}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
