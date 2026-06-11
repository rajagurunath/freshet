"""Batch summarization helpers.

Two batch strategies are supported:

openai-batch
    Build a JSONL file of /v1/chat/completions requests (one per session),
    upload to the OpenAI Files API (purpose="batch"), create an OpenAI Batch
    (completion_window="24h"), store the batch_id, and enqueue a ``batch_poll``
    job (scheduled_for = now + 10 min) to collect results when ready.
    This is the cheap path — OpenAI Batch pricing is ~50% of sync pricing.

local
    Loop each session through an openai-compatible server (e.g. Ollama) running
    locally.  Free when the machine has resources.  Runs synchronously inside the
    job worker thread.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Maximum transcript characters to include per session in a batch request.
_TRANSCRIPT_CHAR_LIMIT = 40_000

_SUMMARY_SYSTEM_PROMPT = """\
You are an expert technical writer summarizing AI coding-assistant sessions
for a company-wide knowledge hub. Produce a concise structured markdown summary
using EXACTLY the following sections (use ### headers):

### Title
One-sentence re-statement of what this session accomplished.

### What Happened
2–4 sentences describing the narrative arc of the session.

### Key Decisions
Bullet list of the most important choices made. Omit if none.

### Artifacts & Files
Bullet list of files created, modified, or deleted. Omit if none.

### Open Questions
Bullet list of unresolved issues or follow-up tasks. Omit if none.

### Business-Relevant Signals
Bullet list of anything relevant to product/business. Omit if purely technical.

Be concise. Do not include any section not listed above.
"""


# ---------------------------------------------------------------------------
# JSONL builder (shared by both providers)
# ---------------------------------------------------------------------------

def _build_transcript(messages: list[dict], char_limit: int = _TRANSCRIPT_CHAR_LIMIT) -> str:
    """Render a list of message dicts to a truncated transcript string.

    When ``COMPRESS_BEFORE_LLM=true``, the joined transcript is compressed via
    headroom-ai before the char-limit guard is applied.  The char limit still
    acts as a final hard cap regardless of whether compression ran.
    """
    parts: list[str] = []
    total = 0
    for msg in messages:
        role = msg.get("role", "unknown")
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        line = f"[{role}] {text}\n"
        if total + len(line) > char_limit:
            parts.append(f"[… transcript truncated at {char_limit} chars …]")
            break
        parts.append(line)
        total += len(line)
    transcript = "".join(parts)

    # Optional pre-LLM compression (headroom-ai); pass-through when disabled.
    from contexthub.llm_compress import compress_text  # lazy import
    transcript, stats = compress_text(transcript)
    if stats.get("enabled"):
        logger.info(
            "_build_transcript: compression saved %d tokens (ratio %.2f)",
            stats.get("tokens_saved", 0),
            stats.get("compression_ratio", 0.0),
        )

    # Final hard char-limit guard (protects against very long compressed output
    # or when compression is disabled).
    if len(transcript) > char_limit:
        transcript = transcript[:char_limit] + f"\n[… transcript truncated at {char_limit} chars …]"

    return transcript


def build_batch_jsonl(
    sessions: list[dict[str, Any]],
    model: str = "gpt-4o-mini",
) -> str:
    """Build a JSONL string suitable for the OpenAI Batch API.

    Each line is a JSON object with:
        custom_id   — the session id (used to correlate responses)
        method      — "POST"
        url         — "/v1/chat/completions"
        body        — chat completions request body

    Args:
        sessions: list of session-like dicts with at least ``id``, ``title``,
                  and ``messages`` (each message has ``role`` and ``text``).
        model:    the chat model to target.

    Returns:
        A newline-separated JSONL string.
    """
    lines: list[str] = []
    for session in sessions:
        session_id = session.get("id", "unknown")
        title = session.get("title", "Untitled")
        messages = session.get("messages") or []
        transcript = _build_transcript(messages)
        user_content = (
            f"Please summarize the following AI coding-assistant session.\n\n"
            f"Session ID: {session_id}\n"
            f"Title: {title}\n\n"
            f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---"
        )
        request_body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 1024,
        }
        line = json.dumps({
            "custom_id": session_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": request_body,
        })
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# openai-batch handler helpers
# ---------------------------------------------------------------------------

def handle_summarize_batch_openai(
    session_ids: list[str],
    sessions_data: list[dict[str, Any]],
    openai_client: Any,
    job_store: Any,
    job_id: str,
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """Upload a JSONL batch to OpenAI and create a batch job.

    Steps:
      1. Build JSONL from sessions_data.
      2. Upload via openai_client.files.create(purpose="batch").
      3. Create batch via openai_client.batches.create(completion_window="24h").
      4. Enqueue a ``batch_poll`` job (scheduled_for = now + 10 min).

    Returns:
        dict with keys: batch_id, file_id, session_count.
    """
    jsonl_content = build_batch_jsonl(sessions_data, model=model)
    jsonl_bytes = jsonl_content.encode("utf-8")
    jsonl_file = io.BytesIO(jsonl_bytes)
    jsonl_file.name = f"batch_{job_id}.jsonl"

    logger.info(
        "handle_summarize_batch_openai: uploading JSONL (%d bytes, %d sessions)",
        len(jsonl_bytes),
        len(sessions_data),
    )

    # Upload file
    file_response = openai_client.files.create(
        file=(jsonl_file.name, jsonl_bytes, "application/jsonl"),
        purpose="batch",
    )
    file_id: str = file_response.id

    # Create the batch
    batch_response = openai_client.batches.create(
        input_file_id=file_id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    batch_id: str = batch_response.id

    logger.info(
        "handle_summarize_batch_openai: created batch %s (file %s)",
        batch_id,
        file_id,
    )

    # Enqueue poll job, scheduled 10 minutes from now
    scheduled_for = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    job_store.enqueue(
        kind="batch_poll",
        payload={
            "batch_id": batch_id,
            "file_id": file_id,
            "session_ids": session_ids,
            "model": model,
            "parent_job_id": job_id,
        },
        scheduled_for=scheduled_for,
    )

    return {
        "batch_id": batch_id,
        "file_id": file_id,
        "session_count": len(sessions_data),
    }


def handle_batch_poll(
    batch_id: str,
    session_ids: list[str],
    openai_client: Any,
    update_summary_fn: Callable[[str, str], None],
    job_store: Optional[Any] = None,
    job_id: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """Poll an OpenAI batch for completion and write summaries.

    If the batch is still in-progress, re-enqueues a new poll job scheduled
    10 minutes from now (exponential back-off could be added later).

    If the batch is complete, parses the output file and calls
    ``update_summary_fn(session_id, summary)`` for each successful response.

    Args:
        batch_id:           OpenAI batch id.
        session_ids:        List of session ids that were submitted.
        openai_client:      An OpenAI client instance.
        update_summary_fn:  Callback to persist a summary for a session.
        job_store:          Optional JobStore (needed to re-enqueue on in_progress).
        job_id:             The current job's id (used in the re-enqueue payload).
        model:              Model used (preserved in re-enqueue payload).

    Returns:
        dict with keys: status, summaries_written (int), errors (int, optional).
    """
    batch = openai_client.batches.retrieve(batch_id)
    status: str = batch.status  # type: ignore[attr-defined]

    if status in ("validating", "in_progress", "finalizing"):
        logger.info("handle_batch_poll: batch %s status=%s — re-scheduling poll", batch_id, status)
        if job_store is not None:
            scheduled_for = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            job_store.enqueue(
                kind="batch_poll",
                payload={
                    "batch_id": batch_id,
                    "session_ids": session_ids,
                    "model": model,
                    "parent_job_id": job_id,
                },
                scheduled_for=scheduled_for,
            )
        return {"status": status, "summaries_written": 0}

    if status == "completed":
        output_file_id: str = batch.output_file_id  # type: ignore[attr-defined]
        file_content = openai_client.files.content(output_file_id)
        raw_bytes: bytes = file_content.content  # type: ignore[attr-defined]
        summaries_written = 0
        errors = 0
        for line in raw_bytes.decode("utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                custom_id: str = entry.get("custom_id", "")
                response_body = entry.get("response", {}).get("body", {})
                choices = response_body.get("choices", [])
                if not choices:
                    errors += 1
                    continue
                content: str = choices[0].get("message", {}).get("content") or ""
                if content:
                    update_summary_fn(custom_id, content)
                    summaries_written += 1
                else:
                    errors += 1
            except Exception as exc:
                logger.warning("handle_batch_poll: failed to parse output line: %s", exc)
                errors += 1

        logger.info(
            "handle_batch_poll: batch %s completed — %d summaries written, %d errors",
            batch_id,
            summaries_written,
            errors,
        )
        result: dict[str, Any] = {"status": "completed", "summaries_written": summaries_written}
        if errors:
            result["errors"] = errors
        return result

    # Failed / cancelled / expired
    logger.warning("handle_batch_poll: batch %s ended with status=%s", batch_id, status)
    return {"status": status, "summaries_written": 0, "errors": len(session_ids)}


# ---------------------------------------------------------------------------
# local handler
# ---------------------------------------------------------------------------

def handle_summarize_batch_local(
    sessions_data: list[dict[str, Any]],
    llm_client: Any,
    update_summary_fn: Callable[[str, str], None],
) -> dict[str, Any]:
    """Summarize sessions one-by-one using a local openai-compatible provider.

    Runs synchronously inside the job worker thread.  Each session is sent
    to ``llm_client.complete()`` independently — errors are caught per-session
    so one bad session does not abort the rest.

    Args:
        sessions_data:      List of session-like dicts (id, title, messages).
        llm_client:         An LLMClient with a ``complete(system, user)`` method.
        update_summary_fn:  Callback to persist each generated summary.

    Returns:
        dict with keys: summaries_written, errors.
    """
    summaries_written = 0
    errors = 0
    for session in sessions_data:
        session_id = session.get("id", "unknown")
        title = session.get("title", "Untitled")
        messages = session.get("messages") or []
        transcript = _build_transcript(messages)
        user_content = (
            f"Please summarize the following AI coding-assistant session.\n\n"
            f"Session ID: {session_id}\n"
            f"Title: {title}\n\n"
            f"--- TRANSCRIPT ---\n{transcript}\n--- END TRANSCRIPT ---"
        )
        try:
            summary = llm_client.complete(_SUMMARY_SYSTEM_PROMPT, user_content, max_tokens=1024)
            if summary:
                update_summary_fn(session_id, summary)
                summaries_written += 1
            else:
                logger.warning("handle_summarize_batch_local: empty summary for session %s", session_id)
                errors += 1
        except Exception as exc:
            logger.warning(
                "handle_summarize_batch_local: failed to summarize session %s: %s",
                session_id,
                exc,
            )
            errors += 1

    return {"summaries_written": summaries_written, "errors": errors}
