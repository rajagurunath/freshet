"""OpenSharing-compatible read surface for the asset hub (Task 15).

Implements the Agent Skill + Volume read surface from the OpenSharing spec
(https://github.com/OpenSharing-IO/OpenSharing — Apache 2.0, LF AI & Data).

Hierarchy:  Share → Schema → Skill (asset of kind=skill).

Endpoints
---------
GET  /opensharing/shares
    List all shares.  We expose a single share named "company".

GET  /opensharing/shares/{share}/schemas
    List schemas (= asset categories) within a share.

GET  /opensharing/shares/{share}/schemas/{schema}/skills
    List skills (assets of kind=skill) within a schema/category.

GET  /opensharing/shares/{share}/skills
    List ALL skills across all schemas in a share.

POST /opensharing/shares/{share}/schemas/{schema}/skills/{skill}/temporary-skill-credentials
    Vend a short-lived download credential (signed token + download URL).
    Local mode  → HMAC-signed URL to GET /v1/assets/{id}/download
    S3 mode     → presigned S3 URL (not implemented here; falls back to local)

Bearer auth reuses the main API keys (same `require_api_key` dependency).

Field names follow the OpenSharing spec:
  - ``name``   — asset/skill name
  - ``schema`` — the schema (category) this skill belongs to
  - ``share``  — the share ("company")
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from contexthub.assets.store import generate_download_token, get_asset_store
from contexthub.config import Settings, get_settings
from contexthub.deps import Caller, require_api_key

logger = logging.getLogger(__name__)

opensharing_router = APIRouter(prefix="/opensharing", tags=["opensharing"])

# The single share exposed by this hub
_COMPANY_SHARE = "company"

# Asset kinds that map to OpenSharing "skills"
_SKILL_KINDS: tuple[str, ...] = ("skill",)


def _asset_to_skill(asset: dict, share: str, schema: str, base_url: str) -> dict:
    """Convert an asset dict to an OpenSharing skill response object.

    Required spec fields: name, schema, share.
    We also include: id, kind, description, version, category, author, created_at,
    download_url_template (informational).
    """
    return {
        "name": asset["name"],
        "schema": schema,
        "share": share,
        # Extension fields (not in spec but useful)
        "id": asset["id"],
        "kind": asset["kind"],
        "description": asset.get("description", ""),
        "version": asset.get("version", ""),
        "category": asset.get("category", ""),
        "author": asset.get("author", ""),
        "created_at": asset.get("created_at", ""),
        "credential_url": (
            f"{base_url}/opensharing/shares/{share}/schemas/{schema}"
            f"/skills/{asset['id']}/temporary-skill-credentials"
        ),
    }


def _request_base_url(request: Request) -> str:
    """Return the scheme+host base URL from the request."""
    return str(request.base_url).rstrip("/")


# ---------------------------------------------------------------------------
# Share listing
# ---------------------------------------------------------------------------

@opensharing_router.get("/shares")
def list_shares(
    request: Request,
    _caller: Caller = Depends(require_api_key),
):
    """List all available shares.

    Currently exposes a single share named "company" representing the
    organisation's internal asset library.
    """
    base = _request_base_url(request)
    return {
        "shares": [
            {
                "name": _COMPANY_SHARE,
                "description": "Company-wide asset library (skills, prompts, scripts, configs)",
                "schemas_url": f"{base}/opensharing/shares/{_COMPANY_SHARE}/schemas",
            }
        ]
    }


# ---------------------------------------------------------------------------
# Schema listing
# ---------------------------------------------------------------------------

@opensharing_router.get("/shares/{share}/schemas")
def list_schemas(
    share: str,
    request: Request,
    caller: Caller = Depends(require_api_key),
):
    """List schemas (asset categories) within a share."""
    if share != _COMPANY_SHARE:
        raise HTTPException(status_code=404, detail=f"Share '{share}' not found.")

    store = get_asset_store()
    categories = store.list_categories()
    base = _request_base_url(request)
    schemas = [
        {
            "name": cat,
            "share": share,
            "skills_url": f"{base}/opensharing/shares/{share}/schemas/{cat}/skills",
        }
        for cat in categories
    ]
    return {"schemas": schemas, "share": share}


# ---------------------------------------------------------------------------
# Skill listing — within a schema
# ---------------------------------------------------------------------------

@opensharing_router.get("/shares/{share}/schemas/{schema}/skills")
def list_skills_in_schema(
    share: str,
    schema: str,
    request: Request,
    caller: Caller = Depends(require_api_key),
):
    """List OpenSharing skills (assets of kind=skill) within a schema/category."""
    if share != _COMPANY_SHARE:
        raise HTTPException(status_code=404, detail=f"Share '{share}' not found.")

    store = get_asset_store()
    assets = store.list_assets(
        kind="skill",
        category=schema,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
    )
    base = _request_base_url(request)
    skills = [_asset_to_skill(a, share, schema, base) for a in assets]
    return {"skills": skills, "schema": schema, "share": share}


# ---------------------------------------------------------------------------
# Skill listing — all skills in a share
# ---------------------------------------------------------------------------

@opensharing_router.get("/shares/{share}/skills")
def list_all_skills(
    share: str,
    request: Request,
    caller: Caller = Depends(require_api_key),
):
    """List ALL skills across all schemas in a share."""
    if share != _COMPANY_SHARE:
        raise HTTPException(status_code=404, detail=f"Share '{share}' not found.")

    store = get_asset_store()
    assets = store.list_assets(
        kind="skill",
        caller_user_id=caller.user_id,
        caller_team=caller.team,
    )
    base = _request_base_url(request)
    skills = [
        _asset_to_skill(a, share, a.get("category", "general"), base)
        for a in assets
    ]
    return {"skills": skills, "share": share}


# ---------------------------------------------------------------------------
# Temporary credential vending
# ---------------------------------------------------------------------------

@opensharing_router.post(
    "/shares/{share}/schemas/{schema}/skills/{skill_id}/temporary-skill-credentials"
)
def temporary_skill_credentials(
    share: str,
    schema: str,
    skill_id: str,
    request: Request,
    caller: Caller = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
):
    """Vend a short-lived (1h) download credential for a skill asset.

    Local mode (default):
        Returns an HMAC-signed token + a signed download URL pointing at
        GET /v1/assets/{id}/download?token=...&expiry=...

    S3 mode (when S3_BUCKET is set):
        Falls back to the same local-signed URL (STS presigned URLs require
        S3 infrastructure which may not be present in every deployment).

    The OpenSharing spec requires the response to include at minimum:
        token   — an opaque credential string
        expiry  — epoch integer when the credential expires
        download_url — the URL to fetch the skill payload
    """
    if share != _COMPANY_SHARE:
        raise HTTPException(status_code=404, detail=f"Share '{share}' not found.")

    store = get_asset_store()
    asset = store.get_asset(
        skill_id,
        caller_user_id=caller.user_id,
        caller_team=caller.team,
        enforce_visibility=True,
    )
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found.")

    # Vend a short-lived signed token (1 hour TTL)
    token, expiry = generate_download_token(
        asset_id=skill_id,
        secret=settings.asset_token_secret,
        ttl_seconds=3600,
    )

    base = _request_base_url(request)
    download_url = (
        f"{base}/v1/assets/{skill_id}/download"
        f"?token={token}&expiry={expiry}"
    )

    return {
        "token": token,
        "expiry": expiry,
        "download_url": download_url,
        "asset_id": skill_id,
        "kind": asset.get("kind", "skill"),
        "name": asset.get("name", ""),
    }
