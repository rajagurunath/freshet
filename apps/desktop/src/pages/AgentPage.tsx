import React, { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Send, Bot, User } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { Select } from "@/components/ui/Select";
import { ToolChip } from "@/components/ToolChip";
import { Markdown } from "@/components/Markdown";
import { AiConsentModal } from "@/components/AiConsentModal";
import { useToast } from "@/components/ui/Toast";
import { useSettings } from "@/store/settings";
import { makeApiClient } from "@/lib/api/client";
import type { Citation, QueryResponse, Category, Tool } from "@/lib/types";
import { CATEGORIES } from "@/lib/types";
import { cn } from "@/components/ui/cn";

const EXAMPLE_QUESTIONS = [
  "What did we decide about pricing?",
  "Summarize the auth migration",
  "What sales objections came up this month?",
  "How does our deployment pipeline work?",
];

interface Message {
  id: string;
  role: "user" | "agent";
  text: string;
  citations?: Citation[];
}

const categoryOptions: { value: string; label: string }[] = [
  { value: "", label: "All categories" },
  ...CATEGORIES.map((c) => ({ value: c, label: c.charAt(0).toUpperCase() + c.slice(1) })),
];

const toolFilterOptions: { value: string; label: string }[] = [
  { value: "", label: "All tools" },
  { value: "claude-code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "kilo-code", label: "Kilo Code" },
];

export function AgentPage() {
  const navigate = useNavigate();
  const settings = useSettings();
  const { error: toastError } = useToast();

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [toolFilter, setToolFilter] = useState("");
  const [useGraph, setUseGraph] = useState(true);
  const [consentOpen, setConsentOpen] = useState(false);
  const pendingQuestion = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const hasApi = Boolean(settings.apiBaseUrl);
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

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return;
    // Gate the first AI use behind explicit consent. Read live store state so
    // a just-granted consent (via the modal) is seen without a stale closure.
    if (!useSettings.getState().aiConsent) {
      pendingQuestion.current = text.trim();
      setConsentOpen(true);
      return;
    }
    const userMsg: Message = {
      id: Date.now().toString(),
      role: "user",
      text: text.trim(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const client = makeApiClient(settings.apiBaseUrl ?? "", settings.apiKey ?? "");
      const filters: Record<string, string> = {};
      if (categoryFilter) filters.category = categoryFilter;
      if (toolFilter) filters.tool = toolFilter;

      const result: QueryResponse = await client.query(
        text.trim(),
        Object.keys(filters).length > 0 ? filters : undefined,
        provider,
        settings.llmModel,
        useGraph,
      );

      const agentMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: "agent",
        text: result.answer,
        citations: result.citations,
      };
      setMessages((prev) => [...prev, agentMsg]);
    } catch {
      toastError("Could not get a response. Check your connection settings.");
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: "agent",
          text: "Sorry, I couldn't connect to the hub. Please check your settings.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleConsentAccept = () => {
    settings.update?.({ aiConsent: true });
    setConsentOpen(false);
    const q = pendingQuestion.current;
    pendingQuestion.current = null;
    if (q) {
      // Re-enter sendMessage now that consent is granted.
      setTimeout(() => sendMessage(q), 0);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  };

  return (
    <div className="flex flex-col h-full max-w-read mx-auto w-full">
      <AiConsentModal
        open={consentOpen}
        providerLabel={providerLabel}
        isLocalAgent={isLocalAgent}
        onAccept={handleConsentAccept}
        onClose={() => {
          pendingQuestion.current = null;
          setConsentOpen(false);
        }}
      />

      {/* Header */}
      <div className="flex items-center justify-between px-6 py-5 border-b border-border bg-bg-elevated shrink-0">
        <div>
          <h1 className="text-h2 font-semibold text-ink">Ask the Agent</h1>
          <p className="text-small text-ink-faint mt-0.5">
            Query your team's collective AI knowledge
          </p>
        </div>
        {/* Filters */}
        <div className="flex items-center gap-2">
          <Select
            options={categoryOptions}
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="w-40 text-small"
          />
          <Select
            options={toolFilterOptions}
            value={toolFilter}
            onChange={(e) => setToolFilter(e.target.value)}
            className="w-36 text-small"
          />
          <label
            className="flex items-center gap-1.5 text-small text-ink-faint cursor-pointer select-none whitespace-nowrap"
            title="Augment retrieval by walking the cross-session knowledge graph (surfaces bridge/alias sessions plain search misses)"
          >
            <input
              type="checkbox"
              checked={useGraph}
              onChange={(e) => setUseGraph(e.target.checked)}
              className="accent-accent"
            />
            Use knowledge graph
          </label>
        </div>
      </div>

      {/* Chat area */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {/* Welcome state */}
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 space-y-6">
            <div className="flex items-center justify-center w-12 h-12 rounded-[12px] bg-accent-wash border border-accent/20">
              <Bot size={22} className="text-accent" />
            </div>
            <div className="text-center space-y-1.5">
              <h2 className="text-h2 font-semibold text-ink">What do you want to know?</h2>
              <p className="text-body text-ink-soft">
                Ask anything about decisions, code, or conversations your team has had with AI.
              </p>
            </div>
            {!hasApi && (
              <div className="bg-accent-wash border border-accent/30 rounded-card px-4 py-3 text-small text-accent-ink">
                Connect a hub in{" "}
                <button
                  className="underline font-medium"
                  onClick={() => navigate("/settings")}
                >
                  Settings
                </button>{" "}
                to use the agent.
              </div>
            )}
            <div className="flex flex-wrap justify-center gap-2 max-w-[480px]">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => sendMessage(q)}
                  className="px-3 py-2 text-small text-ink-soft border border-border rounded-[8px] bg-bg-elevated hover:bg-bg-sunken hover:border-border-strong hover:text-ink transition-all duration-120"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Messages */}
        {messages.map((msg) => (
          <div key={msg.id} className="space-y-3">
            <div
              className={cn(
                "flex items-start gap-3",
                msg.role === "user" && "flex-row-reverse",
              )}
            >
              {/* Avatar */}
              <div
                className={cn(
                  "shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-micro font-medium border",
                  msg.role === "user"
                    ? "bg-accent-wash border-accent/30 text-accent-ink"
                    : "bg-bg-sunken border-border text-ink-faint",
                )}
              >
                {msg.role === "user" ? (
                  <User size={13} />
                ) : (
                  <Bot size={13} />
                )}
              </div>

              {/* Bubble */}
              <div
                className={cn(
                  "max-w-[80%] px-4 py-3 rounded-card text-body",
                  msg.role === "user"
                    ? "bg-accent text-white whitespace-pre-wrap"
                    : "bg-bg-elevated border border-border text-ink",
                )}
              >
                {msg.role === "agent" ? (
                  <Markdown>{msg.text}</Markdown>
                ) : (
                  msg.text
                )}
              </div>
            </div>

            {/* Citations */}
            {msg.citations && msg.citations.length > 0 && (
              <div className="ml-10 space-y-2">
                <p className="text-small font-medium text-ink-faint uppercase tracking-wide text-micro">
                  Sources
                </p>
                <div className="flex flex-col gap-2">
                  {msg.citations.map((c, i) => (
                    <CitationCard
                      key={i}
                      citation={c}
                      onClick={() => navigate(`/sessions/${c.sessionId}`)}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}

        {/* Thinking indicator */}
        {loading && (
          <div className="flex items-start gap-3">
            <div className="shrink-0 w-7 h-7 rounded-full flex items-center justify-center bg-bg-sunken border border-border">
              <Bot size={13} className="text-ink-faint" />
            </div>
            <div className="bg-bg-elevated border border-border rounded-card px-4 py-3 flex items-center gap-2">
              <Spinner size="sm" className="text-ink-faint" />
              <span className="text-small text-ink-faint">Thinking…</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="px-6 py-4 border-t border-border bg-bg-elevated shrink-0">
        <div className="flex items-end gap-2 bg-bg border border-border rounded-card px-3 py-2 focus-within:border-border-strong transition-colors duration-150">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything about your team's AI sessions…"
            rows={1}
            className="flex-1 bg-transparent text-body text-ink placeholder:text-ink-faint resize-none outline-none min-h-[24px] max-h-[120px]"
            style={{ height: "auto" }}
            onInput={(e) => {
              const el = e.target as HTMLTextAreaElement;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 120) + "px";
            }}
          />
          <Button
            variant="primary"
            size="sm"
            loading={loading}
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || !hasApi}
            className="shrink-0 self-end"
          >
            <Send size={13} />
          </Button>
        </div>
        <p className="text-micro text-ink-faint text-center mt-2">
          Press Enter to send · Shift+Enter for new line
        </p>
      </div>
    </div>
  );
}

function CitationCard({ citation, onClick }: { citation: Citation; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="text-left flex flex-col gap-1 p-3 bg-bg-elevated border border-border rounded-[8px] hover:bg-bg-sunken hover:border-border-strong transition-all duration-120 w-full max-w-md"
    >
      <div className="flex items-center gap-2">
        <ToolChip tool={citation.tool} />
        <span className="text-small font-medium text-ink truncate">{citation.title}</span>
        {citation.score !== undefined && (
          <span className="ml-auto text-micro text-ink-faint font-mono">
            {Math.min(100, Math.max(0, Math.round(citation.score * 100)))}%
          </span>
        )}
      </div>
      {citation.author && (
        <span className="text-micro text-ink-faint">{citation.author}</span>
      )}
      <p className="text-small text-ink-soft line-clamp-2 leading-relaxed">{citation.snippet}</p>
    </button>
  );
}
