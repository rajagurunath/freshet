"""Blob store for raw session JSON.

Two backends:
  - LocalBlobStore  — writes files under BLOB_DIR (default, no creds needed)
  - S3BlobStore     — uses boto3; selected when S3_BUCKET is set

The factory function `get_blob_store()` picks the right one automatically.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from contexthub.config import Settings, get_settings


@runtime_checkable
class BlobStore(Protocol):
    """Minimal interface for raw-session storage."""

    def put_session(self, author_id: str, session_id: str, raw_json: str) -> str:
        """Store raw JSON; return the URI (s3://… or file://…)."""
        ...

    def get_session(self, author_id: str, session_id: str) -> Optional[str]:
        """Return the raw JSON string, or None if not found."""
        ...


# ---------------------------------------------------------------------------
# Local filesystem
# ---------------------------------------------------------------------------

class LocalBlobStore:
    """Stores session JSON files under blob_dir/sessions/{author}/{session_id}.json."""

    def __init__(self, blob_dir: str) -> None:
        self._root = Path(blob_dir)

    def _path(self, author_id: str, session_id: str) -> Path:
        return self._root / "sessions" / author_id / f"{session_id}.json"

    def put_session(self, author_id: str, session_id: str, raw_json: str) -> str:
        target = self._path(author_id, session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw_json, encoding="utf-8")
        return f"file://{target.resolve()}"

    def get_session(self, author_id: str, session_id: str) -> Optional[str]:
        target = self._path(author_id, session_id)
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

class S3BlobStore:
    """Stores session JSON in S3 under sessions/{author}/{session_id}.json."""

    def __init__(self, bucket: str, region: str) -> None:
        import boto3  # lazy import

        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    @staticmethod
    def _key(author_id: str, session_id: str) -> str:
        return f"sessions/{author_id}/{session_id}.json"

    def put_session(self, author_id: str, session_id: str, raw_json: str) -> str:
        key = self._key(author_id, session_id)
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=raw_json.encode("utf-8"),
            ContentType="application/json",
        )
        return f"s3://{self._bucket}/{key}"

    def get_session(self, author_id: str, session_id: str) -> Optional[str]:
        import botocore.exceptions  # type: ignore

        key = self._key(author_id, session_id)
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read().decode("utf-8")
        except botocore.exceptions.ClientError:
            return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_blob_store() -> BlobStore:
    """Return a cached BlobStore instance.

    Chooses S3BlobStore when S3_BUCKET is configured, otherwise LocalBlobStore.
    """
    settings = get_settings()
    if settings.s3_bucket:
        return S3BlobStore(bucket=settings.s3_bucket, region=settings.aws_region)
    return LocalBlobStore(blob_dir=settings.blob_dir)
