"""Tests for contexthub.llm_compress — optional headroom-ai pre-LLM compression.

TDD: these tests were written before the implementation and drove the design.

Test matrix
-----------
1. flag off            → passthrough, headroom never imported
2. flag on, no lib     → passthrough with error stat
3. flag on, ok         → compressed text + stats
4. flag on, crash      → passthrough, job-safe
5. below min chars     → passthrough with reason stat
6. integration         → _build_transcript uses compression when flag on
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_settings_cache() -> None:
    """Clear the lru_cache on get_settings so env-var changes take effect."""
    if "contexthub.config" in sys.modules:
        cfg = sys.modules["contexthub.config"]
        if hasattr(cfg, "get_settings") and hasattr(cfg.get_settings, "cache_clear"):
            cfg.get_settings.cache_clear()


def _clear_compress_module() -> None:
    """Remove the cached llm_compress module so it is reimported fresh."""
    for key in list(sys.modules):
        if key in ("contexthub.llm_compress",):
            del sys.modules[key]


def _fresh_compress(monkeypatch, *, compress_before_llm: bool, fake_headroom=None):
    """Return a freshly-imported compress_text with a patched settings singleton.

    Parameters
    ----------
    compress_before_llm:
        Value to inject into Settings.compress_before_llm.
    fake_headroom:
        If not None, inject as sys.modules["headroom"]. Pass ``None`` to let
        the real import resolve (will likely ImportError if headroom isn't
        installed). Pass a fake module object to simulate a successful import.
        Pass the sentinel ``"missing"`` to set sys.modules["headroom"] = None
        (i.e. simulate a previously-failed import guard).
    """
    # Set the env var so Settings() picks it up
    monkeypatch.setenv("COMPRESS_BEFORE_LLM", "true" if compress_before_llm else "false")
    _clear_settings_cache()
    _clear_compress_module()

    # Inject headroom stub into sys.modules before importing llm_compress
    if fake_headroom == "missing":
        monkeypatch.setitem(sys.modules, "headroom", None)  # type: ignore[arg-type]
    elif fake_headroom is not None:
        monkeypatch.setitem(sys.modules, "headroom", fake_headroom)
    else:
        # Ensure no stale headroom entry from a previous test
        sys.modules.pop("headroom", None)

    from contexthub.llm_compress import compress_text
    return compress_text


# ---------------------------------------------------------------------------
# 1. flag off → passthrough, headroom never imported
# ---------------------------------------------------------------------------

class TestFlagOff:
    def test_returns_original_text(self, monkeypatch):
        # Install a fake headroom that RAISES if compress() is called — proves
        # that with the flag off we never touch the library.
        bad_headroom = types.ModuleType("headroom")
        def _explode(*a, **kw):
            raise AssertionError("headroom.compress() must not be called when flag is off")
        bad_headroom.compress = _explode

        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=False,
            fake_headroom=bad_headroom,
        )
        text = "Hello world " * 200  # well above min_chars threshold
        result, stats = compress_text(text)
        assert result == text
        assert stats["enabled"] is False

    def test_does_not_import_headroom(self, monkeypatch):
        """When flag is off, headroom must not appear in sys.modules post-call."""
        # Remove any existing headroom entry
        sys.modules.pop("headroom", None)
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=False,
            fake_headroom=None,
        )
        text = "x" * 3000
        compress_text(text)
        # headroom should still not be in sys.modules (never imported)
        assert "headroom" not in sys.modules


# ---------------------------------------------------------------------------
# 2. flag on, headroom not installed → passthrough with error stat
# ---------------------------------------------------------------------------

class TestHeadroomMissing:
    def test_returns_original_text_with_error_stat(self, monkeypatch):
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom="missing",  # sys.modules["headroom"] = None → ImportError
        )
        text = "x" * 3000
        result, stats = compress_text(text)
        assert result == text
        assert stats["enabled"] is False
        assert stats.get("error") == "not_installed"

    def test_warning_is_logged(self, monkeypatch, caplog):
        import logging
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom="missing",
        )
        with caplog.at_level(logging.WARNING, logger="contexthub.llm_compress"):
            compress_text("x" * 3000)
        assert any("headroom-ai" in r.message.lower() or "headroom" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 3. flag on, fake headroom returns compressed result → compressed text + stats
# ---------------------------------------------------------------------------

def _make_fake_headroom(compressed_content: str, tokens_saved: int = 100, ratio: float = 0.3):
    """Build a fake headroom module with a working compress() function."""
    fake = types.ModuleType("headroom")

    class FakeResult:
        messages = [{"role": "user", "content": compressed_content}]
        tokens_saved = 0
        compression_ratio = 0.0

    fake_result = FakeResult()
    fake_result.tokens_saved = tokens_saved
    fake_result.compression_ratio = ratio

    def fake_compress(messages, model=None, **kw):
        return fake_result

    fake.compress = fake_compress
    return fake


class TestCompressionHappyPath:
    def test_returns_compressed_text(self, monkeypatch):
        compressed = "shorter text here"
        fake = _make_fake_headroom(compressed_content=compressed, tokens_saved=42, ratio=0.25)
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom=fake,
        )
        original = "x" * 3000
        result, stats = compress_text(original)
        assert result == compressed
        assert stats["enabled"] is True
        assert stats["tokens_saved"] == 42
        assert stats["compression_ratio"] == 0.25

    def test_telemetry_env_set_before_import(self, monkeypatch):
        """HEADROOM_TELEMETRY must be set to 'off' before headroom is imported."""
        import os
        seen_telemetry: list[str | None] = []

        fake = types.ModuleType("headroom")

        def fake_compress(messages, model=None, **kw):
            # By the time compress() is called, env var must already be 'off'
            seen_telemetry.append(os.environ.get("HEADROOM_TELEMETRY"))

            class R:
                messages = [{"role": "user", "content": "compressed"}]
                tokens_saved = 0
                compression_ratio = 0.0
            return R()

        fake.compress = fake_compress
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom=fake,
        )
        compress_text("x" * 3000)
        assert seen_telemetry, "compress() was not called"
        assert seen_telemetry[0] == "off", (
            f"HEADROOM_TELEMETRY was {seen_telemetry[0]!r} when compress() ran; expected 'off'"
        )

    def test_model_param_forwarded(self, monkeypatch):
        """model parameter is passed through to headroom.compress."""
        received: list[dict] = []
        fake = types.ModuleType("headroom")

        def fake_compress(messages, model=None, **kw):
            received.append({"model": model})

            class R:
                messages_out = [{"role": "user", "content": "c"}]
                tokens_saved = 0
                compression_ratio = 0.0
            r = R()
            r.messages = r.messages_out
            return r

        fake.compress = fake_compress
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom=fake,
        )
        compress_text("x" * 3000, model="gpt-4o")
        assert received[0]["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# 4. flag on, headroom.compress raises mid-call → passthrough, job-safe
# ---------------------------------------------------------------------------

class TestCompressionCrash:
    def test_exception_returns_original_text(self, monkeypatch):
        fake = types.ModuleType("headroom")

        def boom(messages, model=None, **kw):
            raise RuntimeError("GPU exploded")

        fake.compress = boom
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom=fake,
        )
        text = "x" * 3000
        result, stats = compress_text(text)
        assert result == text
        assert stats["enabled"] is False
        assert "GPU exploded" in stats.get("error", "")

    def test_exception_logged_as_warning(self, monkeypatch, caplog):
        import logging
        fake = types.ModuleType("headroom")
        fake.compress = lambda *a, **kw: (_ for _ in ()).throw(ValueError("oops"))

        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom=fake,
        )
        with caplog.at_level(logging.WARNING, logger="contexthub.llm_compress"):
            compress_text("x" * 3000)
        assert any("oops" in r.message or "compress" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# 5. Below min chars → passthrough
# ---------------------------------------------------------------------------

class TestBelowMinChars:
    def test_short_text_skips_compression(self, monkeypatch):
        # Even with flag on and a working library, short texts must pass through.
        bad_headroom = types.ModuleType("headroom")
        bad_headroom.compress = lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("should not compress short text")
        )
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom=bad_headroom,
        )
        short = "hello world"  # well below 2000 chars
        result, stats = compress_text(short)
        assert result == short
        assert stats["enabled"] is False
        assert stats.get("reason") == "below_min_chars"

    def test_exactly_at_threshold_compresses(self, monkeypatch):
        """Text >= 2000 chars should NOT be skipped."""
        compressed = "shorter"
        fake = _make_fake_headroom(compressed, tokens_saved=10, ratio=0.1)
        compress_text = _fresh_compress(
            monkeypatch,
            compress_before_llm=True,
            fake_headroom=fake,
        )
        text = "a" * 2000
        result, stats = compress_text(text)
        assert result == compressed
        assert stats["enabled"] is True


# ---------------------------------------------------------------------------
# 6. Integration: _build_transcript uses compression when flag on
# ---------------------------------------------------------------------------

class TestBuildTranscriptIntegration:
    """Verify that llm_batch._build_transcript calls compress_text when flag is on."""

    def _setup(self, monkeypatch, compressed_transcript: str):
        """Patch settings + inject fake headroom, return the _build_transcript fn."""
        import importlib

        # ---- settings ----
        monkeypatch.setenv("COMPRESS_BEFORE_LLM", "true")
        _clear_settings_cache()

        # ---- fake headroom (above min_chars threshold) ----
        fake = _make_fake_headroom(compressed_transcript, tokens_saved=50, ratio=0.2)
        monkeypatch.setitem(sys.modules, "headroom", fake)

        # ---- reload llm_compress so it picks up the new settings ----
        _clear_compress_module()

        # ---- reload llm_batch so it picks up fresh llm_compress ----
        if "contexthub.llm_batch" in sys.modules:
            del sys.modules["contexthub.llm_batch"]

        from contexthub.llm_batch import _build_transcript
        return _build_transcript

    def test_build_transcript_compressed(self, monkeypatch):
        compressed = "compressed transcript output"
        _build_transcript = self._setup(monkeypatch, compressed)

        messages = [
            {"role": "user", "text": "x" * 2500},
            {"role": "assistant", "text": "y" * 500},
        ]
        result = _build_transcript(messages)
        assert result == compressed

    def test_build_transcript_flag_off_passthrough(self, monkeypatch):
        """With flag off, _build_transcript should return the uncompressed transcript."""
        import importlib

        monkeypatch.setenv("COMPRESS_BEFORE_LLM", "false")
        _clear_settings_cache()
        _clear_compress_module()

        if "contexthub.llm_batch" in sys.modules:
            del sys.modules["contexthub.llm_batch"]

        from contexthub.llm_batch import _build_transcript
        messages = [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "world"},
        ]
        result = _build_transcript(messages)
        assert "[user] hello" in result
        assert "[assistant] world" in result
