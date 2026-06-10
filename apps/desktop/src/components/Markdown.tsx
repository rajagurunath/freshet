import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "./ui/cn";

interface MarkdownProps {
  children: string;
  className?: string;
}

/**
 * Renders Markdown (GitHub-flavored) with the app's typographic system.
 * Used for assistant/user message bodies and summaries.
 */
export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div
      className={cn(
        "text-body leading-relaxed text-ink break-words",
        "[&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-h2 font-semibold text-ink mt-4 mb-2">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-h3 font-semibold text-ink mt-4 mb-2">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-body font-semibold text-ink mt-3 mb-1.5">{children}</h3>
          ),
          h4: ({ children }) => (
            <h4 className="text-small font-semibold uppercase tracking-wide text-ink-soft mt-3 mb-1">
              {children}
            </h4>
          ),
          p: ({ children }) => <p className="my-2">{children}</p>,
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-accent-ink underline underline-offset-2 hover:text-accent transition-colors"
            >
              {children}
            </a>
          ),
          ul: ({ children }) => (
            <ul className="my-2 pl-5 list-disc marker:text-ink-faint space-y-1">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="my-2 pl-5 list-decimal marker:text-ink-faint space-y-1">{children}</ol>
          ),
          li: ({ children }) => <li className="pl-0.5">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-ink">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-l-2 border-accent/40 pl-3 text-ink-soft italic">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-3 border-border" />,
          code: ({ className: cls, children }) => {
            const text = String(children ?? "");
            const isInline = !cls && !text.includes("\n");
            if (isInline) {
              return (
                <code className="px-1.5 py-0.5 rounded-[5px] bg-bg-sunken border border-border font-mono text-[0.86em] text-accent-ink">
                  {children}
                </code>
              );
            }
            return <code className="font-mono text-small leading-relaxed">{children}</code>;
          },
          pre: ({ children }) => (
            <pre className="my-2.5 p-3 rounded-[8px] bg-bg-sunken border border-border overflow-x-auto text-ink-soft">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="my-2.5 overflow-x-auto rounded-[8px] border border-border">
              <table className="w-full text-small border-collapse">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-bg-sunken">{children}</thead>,
          th: ({ children }) => (
            <th className="text-left font-semibold text-ink px-3 py-1.5 border-b border-border">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-1.5 border-b border-border last:border-0 align-top">
              {children}
            </td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
