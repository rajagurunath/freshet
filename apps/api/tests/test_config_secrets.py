"""Tests for refusing insecure default signing secrets in production.

The asset/share token secrets default to public placeholder values so local
development works out of the box. In any non-dev environment those defaults
must be rejected — otherwise anyone could forge valid HMAC download/share
tokens for any id.

Note: these tests reference ``contexthub.config`` attributes live (not via a
module-level ``from contexthub.config import ...``) because other test modules
call ``importlib.reload(contexthub.config)``, which rebinds the exception class
and would otherwise make ``pytest.raises`` miss the reloaded class.
"""

from __future__ import annotations

import pytest

import contexthub.config as config


@pytest.fixture(autouse=True)
def _clear_secret_env(monkeypatch):
    """Ensure ambient ASSET_/SHARE_TOKEN_SECRET env vars don't leak into these tests.

    Other test fixtures set these in os.environ; pydantic-settings reads env
    vars, so a leaked value would shadow the field default. Clear them so each
    case controls the secrets explicitly via constructor kwargs.
    """
    monkeypatch.delenv("ASSET_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("SHARE_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)


def test_dev_environment_allows_defaults():
    s = config.Settings(environment="development")
    # Does not raise.
    s.require_secure_token_secrets()
    assert s.insecure_default_secrets() == ["asset_token_secret", "share_token_secret"]


@pytest.mark.parametrize("env", ["production", "prod", "staging"])
def test_non_dev_rejects_default_secrets(env):
    s = config.Settings(environment=env)
    with pytest.raises(config.InsecureDefaultSecretError) as exc:
        s.require_secure_token_secrets()
    assert "asset_token_secret" in str(exc.value)
    assert "share_token_secret" in str(exc.value)


def test_non_dev_with_one_default_secret():
    s = config.Settings(
        environment="production",
        asset_token_secret="a-real-random-secret",
        share_token_secret=config.DEFAULT_SHARE_TOKEN_SECRET,
    )
    with pytest.raises(config.InsecureDefaultSecretError) as exc:
        s.require_secure_token_secrets()
    assert "share_token_secret" in str(exc.value)
    assert "asset_token_secret" not in str(exc.value)


def test_non_dev_with_both_secrets_set():
    s = config.Settings(
        environment="production",
        asset_token_secret="real-asset-secret",
        share_token_secret="real-share-secret",
    )
    # Does not raise.
    s.require_secure_token_secrets()
    assert s.insecure_default_secrets() == []


def test_defaults_are_the_documented_placeholders():
    s = config.Settings()
    assert s.asset_token_secret == config.DEFAULT_ASSET_TOKEN_SECRET
    assert s.share_token_secret == config.DEFAULT_SHARE_TOKEN_SECRET
