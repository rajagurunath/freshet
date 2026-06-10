"""Session → text chunks for embedding.

Strategy:
  0. If a summary string is provided, emit it as chunk 0 (high-signal overview).
  1. Walk user + assistant messages (skip pure tool I/O noise, but include
     a one-liner for named tool calls so retrieval can surface tool usage).
  2. Concatenate the filtered message lines into a single transcript string.
  3. Slide a ~1200-char window with 150-char overlap to produce indexable chunks.

Each chunk carries a stable id of the form "{session_id}:{i}" so that
re-ingesting the same session reliably replaces previous chunks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from contexthub.models import NormalizedSession

WINDOW_CHARS = 1200
OVERLAP_CHARS = 150

# Roles whose full text we index
_PROSE_ROLES = {"user", "assistant", "system"}


@dataclass
class Chunk:
    id: str           # "{session_id}:{i}"
    session_id: str
    text: str
    chunk_index: int


def _build_transcript(session: NormalizedSession) -> str:
    """Convert messages to a filtered transcript string.

    - Includes user, assistant, system messages verbatim.
    - For tool messages: emits a short one-liner with the tool_name if present
      (so callers can still retrieve "used Bash / WriteFile" etc.), but skips
      the often-noisy tool output body if it's more than 300 chars.
    """
    lines: list[str] = []
    for msg in session.messages:
        text = (msg.text or "").strip()
        if not text:
            continue

        if msg.role in _PROSE_ROLES:
            lines.append(f"{msg.role}: {text}")
        else:
            # Tool message: emit a brief header, truncate long output
            header = f"tool({msg.tool_name or 'unknown'})"
            if len(text) <= 300:
                lines.append(f"{header}: {text}")
            else:
                lines.append(f"{header}: {text[:300]}…")

    return "\n\n".join(lines)


def _sliding_window(text: str, window: int = WINDOW_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Yield non-empty text slices with a sliding window."""
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + window
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def build_chunks(session: NormalizedSession, summary: Optional[str] = None) -> list[Chunk]:
    """Build the list of indexable Chunk objects for a session.

    Args:
        session: The normalised session.
        summary: An optional pre-computed or user-provided summary string.
                 When given it becomes chunk 0 — the most information-dense
                 piece and thus high-value for retrieval.

    Returns:
        Ordered list of Chunk objects ready for embedding + upsert.
    """
    chunks: list[Chunk] = []
    i = 0

    # Chunk 0 — summary (if available)
    if summary and summary.strip():
        chunks.append(Chunk(
            id=f"{session.id}:{i}",
            session_id=session.id,
            text=summary.strip(),
            chunk_index=i,
        ))
        i += 1

    # Build transcript and slide over it
    transcript = _build_transcript(session)
    if transcript:
        for window_text in _sliding_window(transcript):
            window_text = window_text.strip()
            if not window_text:
                continue
            chunks.append(Chunk(
                id=f"{session.id}:{i}",
                session_id=session.id,
                text=window_text,
                chunk_index=i,
            ))
            i += 1

    return chunks
