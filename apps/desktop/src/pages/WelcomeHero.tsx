import React from "react";
import { useNavigate } from "react-router-dom";
import { Layers, Share2, MessageCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/components/ui/cn";

const features = [
  {
    icon: <Layers size={22} strokeWidth={1.5} />,
    title: "Capture",
    body: "Every AI session — Claude Code, Codex, Kilo Code — indexed automatically as you work.",
  },
  {
    icon: <Share2 size={22} strokeWidth={1.5} />,
    title: "Curate",
    body: "Add context, redact secrets, and push the sessions worth keeping to your team's hub.",
  },
  {
    icon: <MessageCircle size={22} strokeWidth={1.5} />,
    title: "Ask",
    body: "Search across everything your team has ever taught its AI. Get cited answers instantly.",
  },
];

interface WelcomeHeroProps {
  className?: string;
}

export function WelcomeHero({ className }: WelcomeHeroProps) {
  const navigate = useNavigate();

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center min-h-full px-8 py-16",
        className,
      )}
    >
      {/* Mark */}
      <div className="mb-8 flex items-center justify-center w-14 h-14 rounded-[14px] bg-accent-wash border border-accent/20">
        <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
          <path
            d="M5 7C5 5.895 5.895 5 7 5h6v8H5V7zM5 15h8v8H7c-1.105 0-2-.895-2-2v-6zM15 5h6c1.105 0 2 .895 2 2v6h-8V5zM23 15v6c0 1.105-.895 2-2 2h-6v-8h8z"
            fill="#F2541B"
          />
        </svg>
      </div>

      {/* Headline */}
      <h1 className="text-display font-bold text-ink text-center max-w-[560px] leading-tight tracking-tight">
        Everything your team taught the AI, in one place.
      </h1>

      <p className="mt-4 text-h3 text-ink-soft text-center max-w-[440px] font-normal leading-relaxed">
        Freshet captures, curates, and serves up the institutional knowledge locked inside your AI sessions.
      </p>

      {/* CTAs */}
      <div className="flex items-center gap-3 mt-8">
        <Button
          variant="primary"
          size="md"
          onClick={() => navigate("/settings")}
        >
          Connect a hub
        </Button>
        <Button
          variant="secondary"
          size="md"
          onClick={() => navigate("/sessions")}
        >
          Scan my sessions
        </Button>
      </div>

      {/* Divider */}
      <div className="mt-16 mb-12 w-full max-w-[640px] h-px bg-border" />

      {/* Feature grid */}
      <div className="grid grid-cols-3 gap-6 w-full max-w-[640px]">
        {features.map((f) => (
          <div key={f.title} className="flex flex-col gap-3 p-5 bg-bg-elevated border border-border rounded-card">
            <div className="text-accent">{f.icon}</div>
            <div>
              <h3 className="text-h3 font-semibold text-ink">{f.title}</h3>
              <p className="mt-1 text-small text-ink-soft leading-relaxed">{f.body}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
