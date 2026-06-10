"""FastAPI dependency: bearer-token authentication.

Validates the Authorization: Bearer <token> header against the
comma-separated API_KEYS list from Settings.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from contexthub.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=True)


def require_api_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    """Dependency that returns the validated token, or raises 401."""
    token = credentials.credentials
    if token not in settings.api_key_list:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token
