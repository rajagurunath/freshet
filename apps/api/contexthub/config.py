"""Application configuration via pydantic-settings.

All settings can be overridden via environment variables or a .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# Insecure placeholder secrets shipped as defaults for local development.
# Token minting/verification refuses to use these outside a dev environment.
DEFAULT_ASSET_TOKEN_SECRET = "changeme-asset-token-secret"
DEFAULT_SHARE_TOKEN_SECRET = "changeme-share-token-secret"


class InsecureDefaultSecretError(RuntimeError):
    """Raised when a signing secret is left at its insecure default in production."""


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

    # Local / self-hosted LLM (used when llm_provider=local or batch provider=local).
    # Example: Ollama at http://localhost:11434/v1 running mistral or phi3.
    # No API key required for local servers; set local_llm_api_key if the server needs one.
    local_llm_base_url: Optional[str] = None
    local_llm_model: str = "mistral"
    local_llm_api_key: Optional[str] = None

    # Embeddings provider: "local" (sentence-transformers) or "hash" (offline fallback)
    embedding_provider: str = "local"

    # S3 raw-blob storage
    s3_bucket: Optional[str] = None
    aws_region: str = "us-east-1"

    # LanceDB
    lancedb_uri: str = "./data/lancedb"

    # Local blob fallback directory
    blob_dir: str = "./data/blobs"

    # SQLite path for the async jobs queue
    jobs_db: str = "./data/jobs.db"

    # SQLite path for the knowledge graph (Task 13)
    graph_db: str = "./data/graph.db"

    # SQLite path for the rules store (Task 14)
    rules_db: str = "./data/rules.db"

    # ---------------------------------------------------------------------------
    # Cross-session entity resolution (Slice 1)
    # ---------------------------------------------------------------------------
    # Combined (n-gram + embedding) score at/above which two same-kind nodes are
    # linked with a reversible ``same_as`` edge. Below er_low_threshold the pair
    # is rejected; the band in between is reserved for LLM adjudication.
    # Override via ER_HIGH_THRESHOLD / ER_LOW_THRESHOLD.
    er_high_threshold: float = 0.85
    er_low_threshold: float = 0.55

    # ---------------------------------------------------------------------------
    # NER entity extraction (Slice S5)
    # ---------------------------------------------------------------------------
    # Run the deterministic NER pass (regex + gazetteer core, optional spaCy) on
    # ingest to build the entity backbone the graph retrieval arm expands over.
    # The regex core needs no extra deps; spaCy adds person/org entities when the
    # optional ``contexthub[nlp]`` extra is installed. Override via NER_ENABLED etc.
    ner_enabled: bool = True
    ner_use_spacy: bool = True
    # Include granular, mostly session-local kinds (file/function/config/error).
    ner_granular: bool = False

    # SQLite path for the asset hub (Task 15)
    assets_db: str = "./data/assets.db"

    # Local directory for asset ZIP blobs (Task 15)
    asset_blob_dir: str = "./data/asset_blobs"

    # Deployment environment. When not "development"/"dev"/"test", the app
    # refuses to start (and refuses to mint/verify tokens) if either signing
    # secret below is left at its insecure default.
    environment: str = "development"

    # HMAC secret for signing short-lived asset download tokens (Task 15).
    # Override via ASSET_TOKEN_SECRET env var in production.
    asset_token_secret: str = DEFAULT_ASSET_TOKEN_SECRET

    # HMAC secret for signing share tokens on context-page URLs (Task 16).
    # Override via SHARE_TOKEN_SECRET env var in production.
    share_token_secret: str = DEFAULT_SHARE_TOKEN_SECRET

    # Bearer-token allowlist (comma-separated in env, parsed here)
    api_keys: str = "dev-key"

    # CORS origins (comma-separated)
    cors_origins: str = "http://localhost:1420,http://localhost:5173"

    # ---------------------------------------------------------------------------
    # Subscription-window harvester (Task 12)
    # ---------------------------------------------------------------------------
    # Enable the harvest_check job that burns unused weekly subscription quota on
    # summarization / graph extraction right before the provider window resets.
    harvest_enabled: bool = False
    # Comma-separated list of providers to use during harvest.
    # Recognised values: claude-cli, codex-cli, local, openai-batch, default
    harvest_providers: str = "claude-cli,codex-cli"
    # Cron-ish specification for the subscription window reset.
    # Format: "<weekday> <HH:MM>"  e.g. "mon 00:00"
    # Weekday names (case-insensitive): mon tue wed thu fri sat sun
    harvest_window_reset: str = "mon 00:00"
    # How many hours before the window reset to start draining pending work.
    harvest_lookahead_hours: int = 12

    # ---------------------------------------------------------------------------
    # Pre-LLM transcript compression (optional, requires headroom-ai)
    # ---------------------------------------------------------------------------
    # When True, run headroom-ai compression on transcript text before sending
    # it to the LLM. Requires: pip install 'contexthub[compress]'.
    # Set COMPRESS_BEFORE_LLM=true to enable.
    compress_before_llm: bool = False

    @property
    def api_key_list(self) -> list[str]:
        """Return the parsed list of allowed API keys (bare key only, for backward compat)."""
        return [triple[0] for triple in self.api_key_triples]

    @property
    def api_key_triples(self) -> list[tuple[str, str | None, str | None]]:
        """Parse API_KEYS into (key, user_id, team) triples.

        Format: ``key`` (bare, anonymous) or ``key:user_id:team``.
        Comma-separated.  Missing user_id/team fields are returned as None.

        Examples::
            "dev-key"                             → [("dev-key", None, None)]
            "k1:alice:team-red,k2:bob:team-blue"  → [("k1","alice","team-red"),
                                                      ("k2","bob","team-blue")]
            "k1:alice:team-red,k2"                → [("k1","alice","team-red"),
                                                      ("k2", None, None)]
        """
        result: list[tuple[str, str | None, str | None]] = []
        for raw in self.api_keys.split(","):
            raw = raw.strip()
            if not raw:
                continue
            parts = raw.split(":")
            key = parts[0].strip()
            if not key:
                continue
            user_id: str | None = parts[1].strip() if len(parts) >= 2 and parts[1].strip() else None
            team: str | None = parts[2].strip() if len(parts) >= 3 and parts[2].strip() else None
            result.append((key, user_id, team))
        return result

    @property
    def cors_origin_list(self) -> list[str]:
        """Return the parsed list of CORS origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev_environment(self) -> bool:
        """True for local/dev/test environments where insecure defaults are allowed."""
        return self.environment.strip().lower() in ("development", "dev", "local", "test")

    def insecure_default_secrets(self) -> list[str]:
        """Return the names of signing secrets left at their insecure default."""
        offenders: list[str] = []
        if self.asset_token_secret == DEFAULT_ASSET_TOKEN_SECRET:
            offenders.append("asset_token_secret")
        if self.share_token_secret == DEFAULT_SHARE_TOKEN_SECRET:
            offenders.append("share_token_secret")
        return offenders

    def require_secure_token_secrets(self) -> None:
        """Raise in non-dev environments when a signing secret is still the default.

        The default HMAC secrets are public, so anyone could forge valid
        asset-download and context-page share tokens. Refuse to mint or verify
        tokens (and refuse to start) when they are left unset in production.
        """
        if self.is_dev_environment:
            return
        offenders = self.insecure_default_secrets()
        if offenders:
            raise InsecureDefaultSecretError(
                "Refusing to run with insecure default signing secret(s) in "
                f"environment={self.environment!r}: {', '.join(offenders)}. "
                "Set ASSET_TOKEN_SECRET / SHARE_TOKEN_SECRET to random values."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()
