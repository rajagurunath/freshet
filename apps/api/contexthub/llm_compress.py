"""Optional pre-LLM transcript compression via headroom-ai.

Usage::

    from contexthub.llm_compress import compress_text

    text, stats = compress_text(raw_transcript, model="gpt-4o-mini")
    # stats["enabled"] is True when compression ran, False on passthrough.

Requires the optional ``compress`` extra::

    pip install 'contexthub[compress]'

Controlled by the ``COMPRESS_BEFORE_LLM`` environment variable (default: false).
Compression never fails a job — any error returns the original text with
``stats["enabled"] = False``.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Minimum text length worth compressing.  Below this the overhead is not
# worth it and we return the original text immediately.
_MIN_CHARS = 2_000

# Log the "not installed" warning only once per process lifetime.
_warned_not_installed = False


def compress_text(
    text: str,
    *,
    model: str | None = None,
) -> tuple[str, dict]:
    """Compress *text* using headroom-ai, or pass it through unchanged.

    Returns a ``(text, stats)`` tuple.  *stats* always has an ``"enabled"``
    key that is ``True`` when compression was attempted and succeeded.

    When compression is disabled (flag off, library missing, or text too
    short), the original *text* is returned unchanged and ``stats["enabled"]``
    is ``False``.

    Compression never raises — any failure is logged as a warning and the
    original text is returned so the calling job is not blocked.
    """
    from contexthub.config import get_settings

    settings = get_settings()

    # --- flag off: return immediately, never touch headroom ---
    if not settings.compress_before_llm:
        return text, {"enabled": False}

    # --- below threshold: not worth the overhead ---
    if len(text) < _MIN_CHARS:
        return text, {"enabled": False, "reason": "below_min_chars"}

    # --- attempt compression ---
    global _warned_not_installed

    # Privacy: ensure telemetry is off BEFORE importing headroom so that any
    # module-level side effects in the library see the env var set.
    os.environ.setdefault("HEADROOM_TELEMETRY", "off")

    try:
        import headroom as _headroom  # noqa: PLC0415
        if _headroom is None:
            raise ImportError("headroom module is None (previously failed import)")
        compress_fn = _headroom.compress
    except ImportError:
        if not _warned_not_installed:
            logger.warning(
                "headroom-ai not installed; set COMPRESS_BEFORE_LLM=false or "
                "pip install 'contexthub[compress]'"
            )
            _warned_not_installed = True
        return text, {"enabled": False, "error": "not_installed"}

    try:
        messages = [{"role": "user", "content": text}]
        kwargs: dict = {}
        if model is not None:
            kwargs["model"] = model
        result = compress_fn(messages, **kwargs)
        compressed = result.messages[0]["content"]
        return compressed, {
            "enabled": True,
            "tokens_saved": result.tokens_saved,
            "compression_ratio": result.compression_ratio,
        }
    except Exception as exc:
        logger.warning("llm_compress: compression failed, returning original text: %s", exc)
        return text, {"enabled": False, "error": str(exc)}
