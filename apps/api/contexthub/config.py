"""Application configuration via pydantic-settings.

All settings can be overridden via environment variables or a .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM provider for summaries + RAG answers.
    # One of: claude-cli | codex-cli | anthropic | openai
    # Default uses the user's local `claude` CLI — no API key required.
    llm_provider: str = "claude-cli"
    llm_model: str = "sonnet"

    # Optional explicit paths to the local coding-agent CLIs (else found on PATH).
    claude_bin: Optional[str] = None
    codex_bin: Optional[str] = None

    # Anthropic API (used when llm_provider=anthropic)
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-6"

    # OpenAI-compatible API (used when llm_provider=openai).
    # Point base_url at OpenRouter / Ollama / vLLM / LM Studio / OpenAI.
    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: str = "gpt-4o-mini"

    # Embeddings provider: "local" (sentence-transformers) or "hash" (offline fallback)
    embedding_provider: str = "local"

    # S3 raw-blob storage
    s3_bucket: Optional[str] = None
    aws_region: str = "us-east-1"

    # LanceDB
    lancedb_uri: str = "./data/lancedb"

    # Local blob fallback directory
    blob_dir: str = "./data/blobs"

    # Bearer-token allowlist (comma-separated in env, parsed here)
    api_keys: str = "dev-key"

    # CORS origins (comma-separated)
    cors_origins: str = "http://localhost:1420,http://localhost:5173"

    @property
    def api_key_list(self) -> list[str]:
        """Return the parsed list of allowed API keys."""
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        """Return the parsed list of CORS origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
