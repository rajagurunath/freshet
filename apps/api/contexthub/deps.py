"""FastAPI dependency: bearer-token authentication.

Validates the Authorization: Bearer <token> header against the
comma-separated API_KEYS list from Settings.  Returns a Caller object
carrying the resolved user_id and team for the authenticated key.

API_KEYS format (comma-separated):
  key                 — bare key; anonymous full-company access (backward compat)
  key:user_id:team    — full triple with identity and team scope
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from contexthub.config import Settings, get_settings

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=True)


@dataclasses.dataclass
class Caller:
    """Identity of the authenticated API caller."""

    user_id: Optional[str]
    """The caller's user id, or None for anonymous (bare-key) access."""

    team: Optional[str]
    """The caller's team, or None for anonymous (bare-key) access."""


def _resolve_caller(token: str, settings: Settings) -> Caller:
    """Resolve a raw bearer token to a Caller, or raise 401.

    This is a pure function (no FastAPI dependency injection) so it can be
    used in unit tests without wiring up a full request context.
    """
    for key, user_id, team in settings.api_key_triples:
        if token == key:
            if user_id is None:
                logger.warning(
                    "Bare API key used (no user_id/team); granting anonymous full-company access."
                )
            return Caller(user_id=user_id, team=team)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_api_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    settings: Settings = Depends(get_settings),
) -> Caller:
    """Dependency that validates the Bearer token and returns a Caller object.

    Callers with a bare (non-triple) key get Caller(user_id=None, team=None)
    and will see all company-wide sessions but no private/team ones.
    """
    return _resolve_caller(credentials.credentials, settings)
