"""Embeddings abstraction.

Supports two providers:
  - "local"  — sentence-transformers all-MiniLM-L6-v2 (384-dim). Model is
               lazy-loaded on first use so startup is fast.
  - "hash"   — deterministic hashing-based 384-dim vectors; zero external
               deps, works fully offline. Useful for CI and tests.

Add more providers by implementing the Embedder protocol.
"""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from typing import Protocol, runtime_checkable

from contexthub.config import Settings, get_settings

EMBEDDING_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    """Minimal interface every embedder must satisfy."""

    @property
    def dim(self) -> int:
        """Dimensionality of the produced vectors."""
        ...

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding per input text."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Return a single query embedding (may differ from doc embedding)."""
        ...


# ---------------------------------------------------------------------------
# Hash embedder — deterministic, offline, no deps beyond stdlib
# ---------------------------------------------------------------------------

class HashEmbedder:
    """Produces 384-dim float vectors via SHA-256 hashing.

    Not semantically meaningful, but deterministic and fast — ideal for
    CI pipelines and offline environments.
    """

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def _hash_vector(self, text: str) -> list[float]:
        # Map hashed BYTES (0-255) into [-1, 1]. We deliberately do NOT
        # reinterpret raw bytes as IEEE-754 float32 — arbitrary byte patterns
        # decode to NaN/inf (all-1 exponent), which LanceDB rejects. Byte-to-
        # float mapping is always finite. Re-hash with a counter to get enough
        # bytes for the full dimension.
        data = text.encode("utf-8", errors="replace")
        vals: list[float] = []
        counter = 0
        while len(vals) < EMBEDDING_DIM:
            block = hashlib.sha256(data + counter.to_bytes(4, "little")).digest()
            for b in block:
                if len(vals) >= EMBEDDING_DIM:
                    break
                vals.append((b / 255.0) * 2.0 - 1.0)
            counter += 1
        # L2-normalise so cosine similarity behaves sensibly. Guard against a
        # degenerate zero/non-finite norm (effectively impossible, but safe).
        norm = math.sqrt(sum(v * v for v in vals))
        if not math.isfinite(norm) or norm == 0.0:
            return [0.0] * EMBEDDING_DIM
        return [v / norm for v in vals]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._hash_vector(text)


# ---------------------------------------------------------------------------
# Local embedder — sentence-transformers (lazy-loaded)
# ---------------------------------------------------------------------------

class LocalEmbedder:
    """Wraps sentence-transformers all-MiniLM-L6-v2.

    The model is downloaded on first use (~80 MB) and cached in memory.
    Subsequent calls re-use the loaded model without re-initialisation.
    """

    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self) -> None:
        self._model = None  # loaded lazily

    def _ensure_loaded(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._model = SentenceTransformer(self.MODEL_NAME)

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        vecs = self._model.encode(texts, show_progress_bar=False, convert_to_numpy=True)  # type: ignore[union-attr]
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        self._ensure_loaded()
        vec = self._model.encode([text], show_progress_bar=False, convert_to_numpy=True)[0]  # type: ignore[union-attr]
        return vec.tolist()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_embedder(provider: str | None = None) -> Embedder:
    """Return a cached embedder instance for the given provider.

    Falls back to settings.embedding_provider when provider is None.
    """
    settings = get_settings()
    resolved = (provider or settings.embedding_provider).lower()

    if resolved == "hash":
        return HashEmbedder()
    if resolved == "local":
        return LocalEmbedder()

    raise ValueError(
        f"Unknown embedding provider '{resolved}'. "
        "Supported: 'local', 'hash'."
    )
