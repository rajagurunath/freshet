/**
 * Secret redaction utilities.
 * Applies a regex pack over session text before it leaves the machine.
 */
import type { NormalizedSession, SessionMessage } from "./types";

// ─── regex pack ──────────────────────────────────────────────────────────────

const REDACT_PLACEHOLDER = "[REDACTED]";

interface RedactRule {
  label: string;
  pattern: RegExp;
}

const RULES: RedactRule[] = [
  // AWS access key IDs
  { label: "aws-key-id", pattern: /\bAKIA[0-9A-Z]{16}\b/g },
  // AWS secret access keys (40 char base64-ish after assignment)
  {
    label: "aws-secret",
    pattern: /(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)\s*=\s*["']?[A-Za-z0-9/+]{40}["']?/gi,
  },
  // Anthropic / OpenAI-style sk- tokens
  { label: "sk-token", pattern: /\bsk-[A-Za-z0-9_-]{20,}/g },
  // Slack tokens xox*
  { label: "xox-token", pattern: /\bxox[bopas]-[0-9A-Za-z\-]{10,}/g },
  // Bearer tokens in Authorization headers
  {
    label: "bearer-token",
    pattern: /(?:Authorization|authorization)\s*[:=]\s*["']?Bearer\s+[A-Za-z0-9\-._~+/]+=*["']?/g,
  },
  // Generic API key / secret assignments  (KEY=val, SECRET=val, TOKEN=val, PASSWORD=val)
  {
    label: "env-secret",
    pattern: /\b(?:API_KEY|API_SECRET|SECRET_KEY|TOKEN|PASSWORD|PASSWD|AUTH_TOKEN)\s*=\s*["']?[^\s"']{8,}["']?/g,
  },
  // PEM private key blocks
  {
    label: "pem-block",
    pattern: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g,
  },
  // JWTs (three base64url segments separated by dots)
  {
    label: "jwt",
    pattern: /\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g,
  },
];

// ─── core text redactor ───────────────────────────────────────────────────────

export function redactText(text: string): { text: string; count: number } {
  let count = 0;
  let result = text;
  for (const rule of RULES) {
    const replaced = result.replace(rule.pattern, () => {
      count++;
      return REDACT_PLACEHOLDER;
    });
    result = replaced;
  }
  return { text: result, count };
}

// ─── session redactor ────────────────────────────────────────────────────────

function redactMessage(msg: SessionMessage): { msg: SessionMessage; count: number } {
  let totalCount = 0;

  const { text: newText, count: c1 } = redactText(msg.text);
  totalCount += c1;

  let newThinking = msg.thinking;
  if (msg.thinking) {
    const { text: t2, count: c2 } = redactText(msg.thinking);
    newThinking = t2;
    totalCount += c2;
  }

  return {
    msg: { ...msg, text: newText, thinking: newThinking },
    count: totalCount,
  };
}

export function redactSession(
  s: NormalizedSession
): { session: NormalizedSession; count: number } {
  let totalCount = 0;

  const redactedMessages = s.messages.map((m) => {
    const { msg, count } = redactMessage(m);
    totalCount += count;
    return msg;
  });

  const { text: newPreview, count: pc } = redactText(s.preview);
  totalCount += pc;

  const { text: newTitle, count: tc } = redactText(s.title);
  totalCount += tc;

  return {
    session: {
      ...s,
      title: newTitle,
      preview: newPreview,
      messages: redactedMessages,
    },
    count: totalCount,
  };
}
