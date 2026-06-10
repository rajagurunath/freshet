"""Secret redaction for session text.

Applies a regex pack that catches the most common accidental secret leaks:
  - AWS access key IDs (AKIA…)
  - Generic API tokens (sk-…, xox.-…, ghp_…, etc.)
  - Bearer tokens in Authorization headers
  - PEM private key blocks
  - .env-style KEY=value assignments
  - JWTs (three base64url segments separated by dots)
  - Email addresses (optional, off by default)

Returns the redacted text and a count of replacements made.
"""

from __future__ import annotations

import re
from typing import Optional

# Each pattern: (compiled_regex, replacement_string)
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # AWS access key ID  (20-char uppercase alphanumeric starting with AKIA)
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED:AWS_KEY_ID]",
    ),
    # AWS secret access key (40-char base64-ish, often follows the key id)
    (
        re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*\S+"),
        "[REDACTED:AWS_SECRET]",
    ),
    # Generic sk- tokens (OpenAI, Anthropic, Stripe, etc.)
    (
        re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
        "[REDACTED:API_TOKEN]",
    ),
    # Slack / Xoxp tokens
    (
        re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
        "[REDACTED:SLACK_TOKEN]",
    ),
    # GitHub personal access tokens (classic ghp_ or fine-grained github_pat_)
    (
        re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED:GITHUB_TOKEN]",
    ),
    # Bearer tokens in Authorization headers
    (
        re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)\S+"),
        r"\1[REDACTED:BEARER_TOKEN]",
    ),
    # PEM private key blocks
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED:PEM_PRIVATE_KEY]",
    ),
    # .env-style assignments: SECRET_KEY=value, API_KEY="value", etc.
    (
        re.compile(
            r"(?i)(?:password|secret|api[_\-]?key|auth[_\-]?token|access[_\-]?token|private[_\-]?key)"
            r"\s*[=:]\s*['\"]?\S+['\"]?",
        ),
        "[REDACTED:ENV_SECRET]",
    ),
    # JWTs — three base64url segments separated by dots
    (
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"
        ),
        "[REDACTED:JWT]",
    ),
]

# Optional email redaction pattern (not in the default pack)
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")


def redact_text(text: str, redact_emails: bool = False) -> tuple[str, int]:
    """Apply the full redaction regex pack to *text*.

    Args:
        text: The input string, possibly containing secrets.
        redact_emails: Whether to also redact email addresses (default off).

    Returns:
        A (redacted_text, count) tuple where count is the number of
        individual substitutions performed.
    """
    count = 0
    result = text
    for pattern, replacement in _PATTERNS:
        new_result, n = pattern.subn(replacement, result)
        count += n
        result = new_result

    if redact_emails:
        new_result, n = _EMAIL_PATTERN.subn("[REDACTED:EMAIL]", result)
        count += n
        result = new_result

    return result, count
