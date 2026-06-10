import React from "react";
import { ShieldCheck, TerminalSquare, Lock, Sparkles } from "lucide-react";
import { Modal } from "./ui/Modal";
import { Button } from "./ui/Button";

interface AiConsentModalProps {
  open: boolean;
  providerLabel: string;
  /** True when the chosen provider is a local CLI (no data leaves to a vendor API). */
  isLocalAgent: boolean;
  onAccept: () => void;
  onClose: () => void;
}

/**
 * One-time consent before Context Hub uses the user's coding agent (or a
 * configured model provider) to read and analyze a session's contents.
 */
export function AiConsentModal({
  open,
  providerLabel,
  isLocalAgent,
  onAccept,
  onClose,
}: AiConsentModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Use AI to analyze this session?"
      description="Context Hub needs your permission before sending session content to an AI model."
      size="md"
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3 rounded-[8px] bg-bg-sunken border border-border p-3">
          <Sparkles size={18} className="text-accent-ink shrink-0 mt-0.5" />
          <div className="text-small text-ink-soft">
            Provider:{" "}
            <span className="font-medium text-ink">{providerLabel}</span>
            {isLocalAgent ? (
              <p className="mt-1">
                This runs <span className="font-medium text-ink">on your machine</span> using
                your already-authenticated coding agent. No API key is needed and content is
                not sent to Context Hub's servers to be summarized.
              </p>
            ) : (
              <p className="mt-1">
                Session content will be sent to this model provider to generate the summary
                or answer.
              </p>
            )}
          </div>
        </div>

        <ul className="space-y-2.5">
          <ConsentRow icon={<TerminalSquare size={15} />}>
            The session transcript (your prompts + the assistant's replies) is sent to the
            provider above to produce a summary or answer.
          </ConsentRow>
          <ConsentRow icon={<Lock size={15} />}>
            Secrets are redacted before anything is shared to the hub. Nothing is pushed to
            your company hub unless you explicitly choose to.
          </ConsentRow>
          <ConsentRow icon={<ShieldCheck size={15} />}>
            You can change the provider or revoke this anytime in Settings → AI Provider.
          </ConsentRow>
        </ul>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="ghost" size="md" onClick={onClose}>
            Not now
          </Button>
          <Button variant="primary" size="md" onClick={onAccept}>
            Allow &amp; continue
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function ConsentRow({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-3 text-small text-ink-soft">
      <span className="text-ink-faint shrink-0 mt-0.5">{icon}</span>
      <span>{children}</span>
    </li>
  );
}
