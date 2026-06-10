import React, { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Brain,
  Wrench,
  User,
  Sparkles,
} from "lucide-react";
import { cn } from "./ui/cn";
import { Markdown } from "./Markdown";
import type { SessionMessage } from "@/lib/types";

interface MessageBubbleProps {
  message: SessionMessage;
  className?: string;
}

function formatTime(ts?: string): string | null {
  if (!ts) return null;
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function MessageBubble({ message, className }: MessageBubbleProps) {
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [toolOpen, setToolOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const isTool = message.role === "tool" || Boolean(message.toolName);
  const isUser = message.role === "user" && !isTool;
  const isAssistant = message.role === "assistant" && !isTool;
  const isSystem = message.role === "system" && !isTool;
  const time = formatTime(message.timestamp);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };

  // System notices render as a slim divider.
  if (isSystem) {
    return (
      <div className={cn("flex items-center gap-2 py-2 px-5", className)}>
        <div className="h-px flex-1 bg-border" />
        <span className="text-micro text-ink-faint uppercase tracking-wide">System</span>
        <div className="h-px flex-1 bg-border" />
      </div>
    );
  }

  // Tool calls render as a compact, collapsible card (collapsed by default to
  // keep the human conversation readable).
  if (isTool) {
    const hasBody = message.text.trim().length > 0;
    return (
      <div className={cn("px-5 py-2", className)}>
        <div className="rounded-[8px] border border-[#bfe0df] bg-[#f1f8f8] overflow-hidden">
          <button
            onClick={() => hasBody && setToolOpen((v) => !v)}
            className={cn(
              "w-full flex items-center gap-2 px-3 py-2 text-left",
              hasBody && "hover:bg-[#e7f3f2] transition-colors duration-120",
            )}
          >
            <Wrench size={13} className="text-[#15807d] shrink-0" />
            <span className="text-small font-medium text-[#15807d]">
              {message.toolName ?? "Tool"}
            </span>
            {time && <span className="text-micro text-ink-faint font-mono">{time}</span>}
            {hasBody &&
              (toolOpen ? (
                <ChevronDown size={13} className="ml-auto text-[#15807d]" />
              ) : (
                <ChevronRight size={13} className="ml-auto text-[#15807d]" />
              ))}
          </button>
          {hasBody && toolOpen && (
            <pre className="px-3 py-2.5 text-small font-mono text-ink-soft whitespace-pre-wrap break-words bg-white border-t border-[#bfe0df] overflow-x-auto max-h-80">
              {message.text}
            </pre>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={cn("group relative flex gap-3 px-5 py-4", isUser && "bg-bg-sunken/40", className)}>
      {/* Role avatar */}
      <div
        className={cn(
          "w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5",
          isUser
            ? "bg-accent-wash text-accent-ink border border-accent/20"
            : "bg-bg-sunken text-ink-soft border border-border",
        )}
      >
        {isUser ? <User size={14} /> : <Sparkles size={14} />}
      </div>

      <div className="flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center gap-2 mb-1">
          <span className="text-small font-semibold text-ink">
            {isUser ? "You" : "Assistant"}
          </span>
          {message.model && (
            <span className="text-micro text-ink-faint font-mono px-1.5 py-0.5 rounded-full bg-bg-sunken">
              {message.model}
            </span>
          )}
          {time && <span className="text-micro text-ink-faint font-mono">{time}</span>}
          <button
            onClick={handleCopy}
            className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity duration-120 text-ink-faint hover:text-ink"
            aria-label="Copy message"
          >
            {copied ? <Check size={13} className="text-success" /> : <Copy size={13} />}
          </button>
        </div>

        {/* Thinking (collapsible) */}
        {message.thinking && (
          <div className="mb-2 border border-border rounded-[8px] overflow-hidden">
            <button
              onClick={() => setThinkingOpen((v) => !v)}
              className="w-full flex items-center gap-2 px-3 py-1.5 bg-bg-sunken text-small text-ink-soft hover:bg-border/60 transition-colors duration-120"
            >
              <Brain size={13} className="text-ink-faint" />
              <span className="font-medium">Reasoning</span>
              {thinkingOpen ? (
                <ChevronDown size={13} className="ml-auto" />
              ) : (
                <ChevronRight size={13} className="ml-auto" />
              )}
            </button>
            {thinkingOpen && (
              <pre className="px-3 py-3 text-small font-mono text-ink-soft whitespace-pre-wrap break-words bg-bg-sunken border-t border-border overflow-x-auto max-h-96">
                {message.thinking}
              </pre>
            )}
          </div>
        )}

        {/* Body */}
        {message.text.trim() ? (
          <Markdown>{message.text}</Markdown>
        ) : (
          <p className="text-small text-ink-faint italic">(no text content)</p>
        )}
      </div>
    </div>
  );
}
