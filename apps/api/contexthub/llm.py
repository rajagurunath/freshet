"""Pluggable LLM client for summaries and RAG answers.

Freshet does not assume an API key. By default it shells out to the
**user's already-installed coding agent** (the `claude` CLI), so a desktop
install works with zero extra credentials. Teams that prefer hosted models
can point it at any provider:

  - ``claude-cli`` : the local `claude` CLI (Claude Code). No API key. DEFAULT.
  - ``codex-cli``  : the local `codex` CLI (OpenAI Codex). No API key.
  - ``anthropic``  : Anthropic API (needs ``ANTHROPIC_API_KEY``).
  - ``openai``     : any OpenAI-compatible endpoint (OpenRouter, Ollama,
                     vLLM, LM Studio, OpenAI) via ``OPENAI_BASE_URL`` +
                     ``OPENAI_API_KEY``.

``complete()`` raises :class:`LLMError` on failure; callers fall back to a
deterministic stub so the service never hard-fails.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional, Protocol, runtime_checkable

from contexthub.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Providers that run a local CLI (the user's own coding agent). These need no
# API key and are safe to drive from a per-request override.
CLI_PROVIDERS = {"claude-cli", "codex-cli"}


class LLMError(RuntimeError):
    """Raised when an LLM provider is unavailable or a call fails."""


@runtime_checkable
class LLMClient(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str: ...


def _combine(system: str, user: str) -> str:
    return f"{system.strip()}\n\n{user}" if system and system.strip() else user


# ---------------------------------------------------------------------------
# Local CLI providers (use the user's existing coding-agent auth)
# ---------------------------------------------------------------------------

class ClaudeCLI:
    """Drives the local `claude` CLI in non-interactive print mode."""

    name = "claude-cli"

    def __init__(self, model: str = "sonnet", bin_path: Optional[str] = None, timeout: int = 180):
        self.model = model or "sonnet"
        self.bin = bin_path or shutil.which("claude")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.bin)

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        if not self.bin:
            raise LLMError("`claude` CLI not found on PATH")
        cmd = [self.bin, "-p", "--model", self.model]
        try:
            proc = subprocess.run(
                cmd,
                input=_combine(system, user),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"`claude` CLI timed out after {self.timeout}s") from exc
        if proc.returncode != 0:
            raise LLMError(f"`claude` CLI failed: {proc.stderr.strip()[:300]}")
        out = proc.stdout.strip()
        if not out:
            raise LLMError("`claude` CLI returned empty output")
        return out


class CodexCLI:
    """Drives the local `codex` CLI in non-interactive exec mode."""

    name = "codex-cli"

    def __init__(self, model: str = "", bin_path: Optional[str] = None, timeout: int = 180):
        self.model = model or ""  # empty → codex's own configured default
        self.bin = bin_path or shutil.which("codex")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.bin)

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        if not self.bin:
            raise LLMError("`codex` CLI not found on PATH")
        # `codex exec` writes its final assistant message to --output-last-message.
        fd, out_path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            cmd = [self.bin, "exec", "--skip-git-repo-check", "--output-last-message", out_path]
            if self.model:
                cmd += ["-m", self.model]
            try:
                proc = subprocess.run(
                    cmd,
                    input=_combine(system, user),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"`codex` CLI timed out after {self.timeout}s") from exc
            try:
                with open(out_path, encoding="utf-8") as fh:
                    out = fh.read().strip()
            except OSError:
                out = ""
            if not out:
                out = proc.stdout.strip()
            if proc.returncode != 0 and not out:
                raise LLMError(f"`codex` CLI failed: {proc.stderr.strip()[:300]}")
            if not out:
                raise LLMError("`codex` CLI returned empty output")
            return out
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Hosted API providers
# ---------------------------------------------------------------------------

class AnthropicAPI:
    """Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, model: str, api_key: Optional[str]):
        self.model = model
        self.api_key = api_key

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        try:
            import anthropic  # lazy import
        except ImportError as exc:
            raise LLMError("anthropic package not installed") from exc
        client = anthropic.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text  # type: ignore[index]


class OpenAICompatible:
    """Any OpenAI-compatible chat-completions endpoint.

    Works with OpenRouter, Ollama, vLLM, LM Studio, and OpenAI itself —
    just point ``base_url`` at the right host.
    """

    name = "openai"

    def __init__(self, model: str, api_key: Optional[str], base_url: Optional[str]):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url or None

    def available(self) -> bool:
        # Local servers (Ollama/LM Studio) often need no key, just a base_url.
        return bool(self.api_key or self.base_url)

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        try:
            from openai import OpenAI  # lazy import
        except ImportError as exc:
            raise LLMError("openai package not installed") from exc
        client = OpenAI(api_key=self.api_key or "not-needed", base_url=self.base_url)
        messages = []
        if system and system.strip():
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_llm(
    settings: Optional[Settings] = None,
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
) -> LLMClient:
    """Build an LLM client.

    Resolution order for provider/model: explicit override → settings → default.
    Secrets (API keys, base URLs) always come from server settings, never from
    a per-request override, so a shared API never leaks credentials.
    """
    settings = settings or get_settings()
    provider = (provider_override or settings.llm_provider or "claude-cli").lower()
    model = model_override or settings.llm_model or ""

    if provider == "claude-cli":
        return ClaudeCLI(model=model or "sonnet", bin_path=settings.claude_bin or None)
    if provider == "codex-cli":
        return CodexCLI(model=model, bin_path=settings.codex_bin or None)
    if provider == "anthropic":
        return AnthropicAPI(model=model or settings.anthropic_model, api_key=settings.anthropic_api_key)
    if provider == "openai":
        return OpenAICompatible(
            model=model or settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
    if provider == "local":
        # Local / self-hosted openai-compatible server (Ollama, LM Studio, vLLM, etc.)
        # Falls back to openai base_url if local_llm_base_url is not set.
        local_client = OpenAICompatible(
            model=model or settings.local_llm_model or "mistral",
            api_key=settings.local_llm_api_key or settings.openai_api_key or "not-needed",
            base_url=settings.local_llm_base_url or settings.openai_base_url,
        )
        local_client.name = "local"  # type: ignore[assignment]
        return local_client
    raise LLMError(
        f"Unknown LLM provider '{provider}'. "
        "Supported: claude-cli, codex-cli, anthropic, openai, local."
    )


_PROVIDER_LABELS = {
    "claude-cli": "Claude (local CLI)",
    "codex-cli": "Codex (local CLI)",
    "anthropic": "Anthropic API",
    "openai": "OpenAI-compatible",
    "local": "Local model (Ollama/vLLM)",
}


def available_providers(settings: Optional[Settings] = None) -> list[dict]:
    """Report which providers are usable on this host (for the desktop UI)."""
    settings = settings or get_settings()
    out: list[dict] = []
    for pid, label in _PROVIDER_LABELS.items():
        try:
            usable = get_llm(settings, provider_override=pid).available()
        except LLMError:
            usable = False
        out.append({
            "id": pid,
            "label": label,
            "available": usable,
            "is_default": pid == (settings.llm_provider or "claude-cli").lower(),
            "needs_key": pid in {"anthropic", "openai"},
        })
    return out
