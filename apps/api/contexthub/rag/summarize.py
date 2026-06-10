"""Session summarization via Claude.

Builds a structured markdown summary covering:
  - What happened (narrative)
  - Key decisions made
  - Artifacts / files touched
  - Open questions left in the session
  - Business-relevant signals (sales, marketing, product, engineering)

Falls back to an extractive stub (first user message + assistant header
bullets) when no API key is configured, clearly marked as a stub so
callers can treat it accordingly.
"""

from __future__ import annotations

import logging
from typing import Optional

from contexthub.config import Settings
from contexthub.llm import LLMError, get_llm
from contexthub.models import NormalizedSession

logger = logging.getLogger(__name__)

# Keep the transcript under this many characters before sending to Claude.
# Claude's context window is large, but we budget conservatively to leave
# room for the response and any system-prompt overhead.
_TRANSCRIPT_CHAR_BUDGET = 40_000

_SYSTEM_PROMPT = """\
You are an expert technical writer summarizing AI coding-assistant sessions
for a company-wide knowledge hub. Produce a concise structured markdown summary
using EXACTLY the following sections (use ### headers):

### Title
One-sentence re-statement of what this session accomplished.

### What Happened
2–4 sentences describing the narrative arc of the session.

### Key Decisions
Bullet list of the most important choices made (architecture, libraries,
approaches, trade-offs). Omit if none.

### Artifacts & Files
Bullet list of files created, modified, or deleted. Omit if none.

### Open Questions
Bullet list of unresolved issues, TODOs, or follow-up tasks. Omit if none.

### Business-Relevant Signals
Bullet list of anything relevant to sales, marketing, product strategy,
customer pain points, or revenue. Omit if purely technical with no business
relevance.

Be concise. Do not include any section not listed above. Do not repeat the
raw conversation verbatim.
"""


def _build_transcript(session: NormalizedSession, char_budget: int = _TRANSCRIPT_CHAR_BUDGET) -> str:
    """Render messages to a condensed transcript string, honouring the char budget."""
    parts: list[str] = []
    total = 0
    for msg in session.messages:
        text = (msg.text or "").strip()
        if not text:
            continue
        line = f"[{msg.role}] {text}\n"
        if total + len(line) > char_budget:
            parts.append(f"[… transcript truncated at {char_budget} chars …]")
            break
        parts.append(line)
        total += len(line)
    return "".join(parts)


def _extractive_stub(session: NormalizedSession) -> str:
    """Return a clearly-marked extractive stub when no LLM is available."""
    first_user = next(
        (m.text for m in session.messages if m.role == "user" and m.text.strip()),
        "(no user messages found)",
    )
    assistant_headers = [
        m.text[:120] for m in session.messages if m.role == "assistant" and m.text.strip()
    ][:5]

    bullet_lines = "\n".join(f"- {h}" for h in assistant_headers) or "- (no assistant messages)"

    return (
        f"<!-- STUB: no LLM provider available. This is an extractive stub. -->\n\n"
        f"### Title\n{session.title or 'Untitled session'}\n\n"
        f"### What Happened\nFirst user message: {first_user[:300]}\n\n"
        f"### Key Decisions\n{bullet_lines}\n\n"
        f"### Open Questions\n- (stub: configure an LLM provider for full analysis)\n"
    )


def summarize_session(
    session: NormalizedSession,
    settings: Settings,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Produce a structured summary for *session*.

    Uses the configured LLM provider (default: the user's local `claude` CLI).
    Falls back to a clearly-labelled extractive stub if no provider is
    available or the call fails, so the service never hard-fails.
    """
    try:
        llm = get_llm(settings, provider_override=provider, model_override=model)
        if not llm.available():
            logger.info("LLM provider '%s' unavailable; extractive stub for %s", llm.name, session.id)
            return _extractive_stub(session)

        transcript = _build_transcript(session)
        user_content = (
            f"Please summarize the following AI coding-assistant session.\n\n"
            f"Session ID: {session.id}\n"
            f"Tool: {session.tool}\n"
            f"Title: {session.title}\n"
            f"Project: {session.project or 'unknown'}\n\n"
            f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---"
        )
        return llm.complete(_SYSTEM_PROMPT, user_content, max_tokens=1024)

    except LLMError as exc:
        logger.warning("Summarization via LLM failed for %s: %s", session.id, exc)
        return _extractive_stub(session)
    except Exception as exc:  # defensive: never fail ingestion on summary error
        logger.warning("Unexpected summarization error for %s: %s", session.id, exc)
        return _extractive_stub(session)
