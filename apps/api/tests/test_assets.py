"""Tests for Task 15: Asset hub + OpenSharing-compatible surface (API half).

Covers:
  1. AssetStore: create, list, get, delete assets.
  2. POST /v1/assets     — multipart upload (stores metadata + payload zip).
  3. GET  /v1/assets     — list with FTS over name+description, kind/category filter.
  4. GET  /v1/assets/{id}    — fetch single asset.
  5. GET  /v1/assets/{id}/download — signed download token validation + redirect / file.
  6. OpenSharing surface:
       GET /opensharing/shares
       GET /opensharing/shares/{share}/schemas
       GET /opensharing/shares/{share}/schemas/{schema}/skills
       POST /opensharing/shares/{share}/schemas/{schema}/skills/{skill}/temporary-skill-credentials
  7. Signed token: correct HMAC validates; expired / wrong HMAC → 403.
  8. Visibility: private asset not visible to another caller in list/get.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import sys
import tempfile
import time
import zipfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Cache reset helper (same pattern as other test files)
# ---------------------------------------------------------------------------

def _clear_caches() -> None:
    mods = [
        "contexthub.config",
        "contexthub.embeddings",
        "contexthub.storage.blob",
        "contexthub.storage.vectors",
        "contexthub.jobs.store",
        "contexthub.graph.store",
        "contexthub.rules.store",
        "contexthub.assets.store",
    ]
    for mod_name in mods:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            for attr in dir(mod):
                fn = getattr(mod, attr, None)
                if callable(fn) and hasattr(fn, "cache_clear"):
                    fn.cache_clear()
    if "contexthub.storage.vectors" in sys.modules:
        sys.modules["contexthub.storage.vectors"].reset_vector_store()
    if "contexthub.graph.store" in sys.modules:
        sys.modules["contexthub.graph.store"].reset_graph_store()
    if "contexthub.rules.store" in sys.modules:
        sys.modules["contexthub.rules.store"].reset_rules_store()
    if "contexthub.assets.store" in sys.modules:
        sys.modules["contexthub.assets.store"].reset_asset_store()


# ===========================================================================
# Unit tests: AssetStore (no HTTP)
# ===========================================================================

class TestAssetStore:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        from contexthub.assets.store import AssetStore
        self.store = AssetStore(
            db_path=os.path.join(self._tmpdir.name, "assets.db"),
            blob_dir=os.path.join(self._tmpdir.name, "blobs"),
        )

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_create_returns_id(self):
        aid = self.store.create_asset(
            kind="skill",
            name="My Skill",
            description="A useful skill",
            category="general",
            author="alice",
            team="team-red",
            visibility="company",
            files_json="[]",
            blob_uri="file:///tmp/foo.zip",
            version="1.0.0",
        )
        assert isinstance(aid, str) and aid

    def test_get_asset_found(self):
        aid = self.store.create_asset(
            kind="prompt",
            name="Code review prompt",
            description="Reviews code",
            category="engineering",
            author="bob",
            team=None,
            visibility="company",
            files_json="[]",
            blob_uri="file:///tmp/bar.zip",
            version="0.1",
        )
        asset = self.store.get_asset(aid)
        assert asset is not None
        assert asset["name"] == "Code review prompt"
        assert asset["kind"] == "prompt"

    def test_get_asset_not_found(self):
        assert self.store.get_asset("nonexistent") is None

    def test_list_assets_empty(self):
        results = self.store.list_assets()
        assert isinstance(results, list)

    def test_list_assets_by_kind(self):
        self.store.create_asset(
            kind="skill", name="Skill A", description="desc",
            category="general", author="alice", team=None,
            visibility="company", files_json="[]", blob_uri="file:///a.zip", version="1",
        )
        self.store.create_asset(
            kind="config", name="Config B", description="cfg",
            category="ops", author="alice", team=None,
            visibility="company", files_json="[]", blob_uri="file:///b.zip", version="1",
        )
        skills = self.store.list_assets(kind="skill")
        configs = self.store.list_assets(kind="config")
        assert all(a["kind"] == "skill" for a in skills)
        assert all(a["kind"] == "config" for a in configs)

    def test_list_assets_fts(self):
        self.store.create_asset(
            kind="prompt", name="Python linter helper", description="Runs flake8",
            category="engineering", author="carol", team=None,
            visibility="company", files_json="[]", blob_uri="file:///c.zip", version="1",
        )
        self.store.create_asset(
            kind="prompt", name="Rust builder", description="Compiles Rust code",
            category="engineering", author="carol", team=None,
            visibility="company", files_json="[]", blob_uri="file:///d.zip", version="1",
        )
        results = self.store.list_assets(q="linter")
        names = [a["name"] for a in results]
        assert "Python linter helper" in names
        assert "Rust builder" not in names

    def test_list_assets_fts_description(self):
        self.store.create_asset(
            kind="script", name="Utility X", description="This script runs mypy on the project",
            category="engineering", author="dave", team=None,
            visibility="company", files_json="[]", blob_uri="file:///e.zip", version="1",
        )
        results = self.store.list_assets(q="mypy")
        assert any(a["name"] == "Utility X" for a in results)

    def test_visibility_private_not_listed_by_other(self):
        self.store.create_asset(
            kind="skill", name="Private Skill", description="secret",
            category="general", author="alice", team=None,
            visibility="private", files_json="[]", blob_uri="file:///p.zip", version="1",
        )
        # bob lists — should NOT see alice's private asset
        results = self.store.list_assets(
            caller_user_id="bob", caller_team=None
        )
        assert not any(a["name"] == "Private Skill" for a in results)

    def test_visibility_private_visible_to_owner(self):
        self.store.create_asset(
            kind="skill", name="My Private Skill", description="mine",
            category="general", author="alice", team=None,
            visibility="private", files_json="[]", blob_uri="file:///pp.zip", version="1",
        )
        results = self.store.list_assets(
            caller_user_id="alice", caller_team=None
        )
        assert any(a["name"] == "My Private Skill" for a in results)

    def test_visibility_team_not_visible_to_other_team(self):
        self.store.create_asset(
            kind="skill", name="Team Red Skill", description="red team only",
            category="general", author="alice", team="team-red",
            visibility="team", files_json="[]", blob_uri="file:///t.zip", version="1",
        )
        results = self.store.list_assets(caller_user_id="bob", caller_team="team-blue")
        assert not any(a["name"] == "Team Red Skill" for a in results)

    def test_visibility_team_visible_to_same_team(self):
        self.store.create_asset(
            kind="skill", name="Team Blue Skill", description="blue team",
            category="general", author="carol", team="team-blue",
            visibility="team", files_json="[]", blob_uri="file:///tb.zip", version="1",
        )
        results = self.store.list_assets(caller_user_id="dan", caller_team="team-blue")
        assert any(a["name"] == "Team Blue Skill" for a in results)

    def test_delete_asset(self):
        aid = self.store.create_asset(
            kind="skill", name="To Delete", description="gone soon",
            category="general", author="alice", team=None,
            visibility="company", files_json="[]", blob_uri="file:///del.zip", version="1",
        )
        assert self.store.get_asset(aid) is not None
        self.store.delete_asset(aid)
        assert self.store.get_asset(aid) is None


# ===========================================================================
# Signed download token tests (unit, no HTTP)
# ===========================================================================

class TestSignedToken:
    def test_generate_and_verify(self):
        from contexthub.assets.store import generate_download_token, verify_download_token
        secret = "test-secret"
        asset_id = "asset-abc-123"
        token, expiry = generate_download_token(asset_id, secret, ttl_seconds=300)
        assert token
        assert verify_download_token(asset_id, token, expiry, secret) is True

    def test_expired_token_rejected(self):
        from contexthub.assets.store import generate_download_token, verify_download_token
        secret = "test-secret"
        asset_id = "asset-xyz"
        # Generate a token that expired in the past
        expiry = int(time.time()) - 1  # 1 second ago
        msg = f"{asset_id}:{expiry}".encode()
        sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
        assert verify_download_token(asset_id, sig, expiry, secret) is False

    def test_wrong_secret_rejected(self):
        from contexthub.assets.store import generate_download_token, verify_download_token
        secret = "correct-secret"
        asset_id = "asset-lmn"
        token, expiry = generate_download_token(asset_id, secret, ttl_seconds=300)
        assert verify_download_token(asset_id, token, expiry, "wrong-secret") is False

    def test_tampered_asset_id_rejected(self):
        from contexthub.assets.store import generate_download_token, verify_download_token
        secret = "test-secret"
        token, expiry = generate_download_token("asset-original", secret, ttl_seconds=300)
        # Try to use the same token for a different asset
        assert verify_download_token("asset-different", token, expiry, secret) is False


# ===========================================================================
# HTTP integration tests
# ===========================================================================

@pytest.fixture(scope="module")
def tmp_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield (
            os.path.join(tmpdir, "lancedb"),
            os.path.join(tmpdir, "blobs"),
            os.path.join(tmpdir, "jobs.db"),
            os.path.join(tmpdir, "graph.db"),
            os.path.join(tmpdir, "rules.db"),
            os.path.join(tmpdir, "assets.db"),
            os.path.join(tmpdir, "asset_blobs"),
        )


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db, graph_db, rules_db, assets_db, asset_blobs = tmp_dirs

    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "JOBS_DB": jobs_db,
        "GRAPH_DB": graph_db,
        "RULES_DB": rules_db,
        "ASSETS_DB": assets_db,
        "ASSET_BLOB_DIR": asset_blobs,
        "API_KEYS": "alice-key:alice:team-red,bob-key:bob:team-blue",
        "ANTHROPIC_API_KEY": "",
        "LLM_PROVIDER": "anthropic",
        "S3_BUCKET": "",
        "CORS_ORIGINS": "",
        "ASSET_TOKEN_SECRET": "integration-test-secret",
    }
    original_env = {k: os.environ.get(k) for k in env_patch}
    os.environ.update(env_patch)

    _clear_caches()

    from contexthub.main import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _clear_caches()


ALICE = {"Authorization": "Bearer alice-key"}
BOB = {"Authorization": "Bearer bob-key"}


def _make_zip(content: str = "print('hello')") -> bytes:
    """Build a minimal in-memory zip file for upload tests."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("main.py", content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Asset CRUD via HTTP
# ---------------------------------------------------------------------------

def test_upload_skill(client: TestClient):
    """POST /v1/assets with multipart form should store and index a skill."""
    zip_bytes = _make_zip("print('skill')")
    resp = client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "Hello World Skill",
            "description": "Prints hello world",
            "category": "general",
            "visibility": "company",
            "version": "1.0.0",
        },
        files={"file": ("skill.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "Hello World Skill"
    assert data["kind"] == "skill"
    assert "id" in data
    return data["id"]


def test_list_assets_returns_uploaded(client: TestClient):
    """GET /v1/assets should return the uploaded skill."""
    # Upload one first
    zip_bytes = _make_zip()
    client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "List Test Skill",
            "description": "For listing test",
            "category": "general",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("skill.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )

    resp = client.get("/v1/assets", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    names = [a["name"] for a in data["items"]]
    assert "List Test Skill" in names


def test_list_assets_filter_kind(client: TestClient):
    """GET /v1/assets?kind=prompt should only return prompt assets."""
    zip_bytes = _make_zip()
    client.post(
        "/v1/assets",
        data={
            "kind": "prompt",
            "name": "A Prompt Asset",
            "description": "For kind filter test",
            "category": "engineering",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("prompt.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )

    resp = client.get("/v1/assets", params={"kind": "prompt"}, headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert all(a["kind"] == "prompt" for a in data["items"])


def test_list_assets_fts_search(client: TestClient):
    """GET /v1/assets?q=<term> should filter by name/description FTS."""
    zip_bytes = _make_zip()
    client.post(
        "/v1/assets",
        data={
            "kind": "script",
            "name": "Deployment Automation Script",
            "description": "Automates blue-green deployments",
            "category": "ops",
            "visibility": "company",
            "version": "2.0",
        },
        files={"file": ("deploy.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )

    resp = client.get("/v1/assets", params={"q": "deployment"}, headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    names = [a["name"] for a in data["items"]]
    assert "Deployment Automation Script" in names


def test_get_asset_by_id(client: TestClient):
    """GET /v1/assets/{id} should return the asset metadata."""
    zip_bytes = _make_zip()
    create_resp = client.post(
        "/v1/assets",
        data={
            "kind": "config",
            "name": "Config For Get Test",
            "description": "Get endpoint test",
            "category": "ops",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("cfg.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["id"]

    resp = client.get(f"/v1/assets/{asset_id}", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == asset_id
    assert data["name"] == "Config For Get Test"


def test_get_asset_not_found(client: TestClient):
    resp = client.get("/v1/assets/nonexistent-id", headers=ALICE)
    assert resp.status_code == 404


def test_download_asset(client: TestClient):
    """GET /v1/assets/{id}/download should return a signed URL or file."""
    zip_bytes = _make_zip("print('downloadable')")
    create_resp = client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "Downloadable Skill",
            "description": "Can be downloaded",
            "category": "general",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("dl.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["id"]

    resp = client.get(f"/v1/assets/{asset_id}/download", headers=ALICE)
    # Either 200 (file content) or 307/302 (redirect to signed URL)
    assert resp.status_code in (200, 302, 307), resp.text


def test_download_not_found(client: TestClient):
    resp = client.get("/v1/assets/no-such-asset/download", headers=ALICE)
    assert resp.status_code == 404


def test_private_asset_not_visible_to_other(client: TestClient):
    """A private asset created by alice should not appear in bob's list."""
    zip_bytes = _make_zip()
    create_resp = client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "Alice Private Asset",
            "description": "Not for bob",
            "category": "general",
            "visibility": "private",
            "version": "1.0",
        },
        files={"file": ("private.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["id"]

    # Bob should not see it in list
    resp = client.get("/v1/assets", headers=BOB)
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()["items"]]
    assert "Alice Private Asset" not in names

    # Bob should get 404 on direct fetch
    resp2 = client.get(f"/v1/assets/{asset_id}", headers=BOB)
    assert resp2.status_code == 404


def test_private_asset_visible_to_owner(client: TestClient):
    """A private asset should be visible to its owner."""
    zip_bytes = _make_zip()
    create_resp = client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "Alice Own Private Asset",
            "description": "Mine",
            "category": "general",
            "visibility": "private",
            "version": "1.0",
        },
        files={"file": ("ownprivate.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["id"]

    resp = client.get(f"/v1/assets/{asset_id}", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json()["id"] == asset_id


# ---------------------------------------------------------------------------
# OpenSharing surface
# ---------------------------------------------------------------------------

def test_opensharing_list_shares(client: TestClient):
    """GET /opensharing/shares should list the 'company' share."""
    resp = client.get("/opensharing/shares", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert "shares" in data
    share_names = [s["name"] for s in data["shares"]]
    assert "company" in share_names


def test_opensharing_list_schemas(client: TestClient):
    """GET /opensharing/shares/company/schemas should return asset categories as schemas."""
    resp = client.get("/opensharing/shares/company/schemas", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert "schemas" in data


def test_opensharing_list_skills_in_schema(client: TestClient):
    """GET /opensharing/shares/company/schemas/{schema}/skills should list skills."""
    # Ensure at least one skill exists in 'general' schema
    zip_bytes = _make_zip()
    client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "OpenSharing Test Skill",
            "description": "For OpenSharing listing",
            "category": "general",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("os_skill.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )

    resp = client.get("/opensharing/shares/company/schemas/general/skills", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    # Check field names per OpenSharing spec: name, schema, share
    skill_names = [s["name"] for s in data["skills"]]
    assert "OpenSharing Test Skill" in skill_names
    # Verify each skill has the required spec fields
    for skill in data["skills"]:
        assert "name" in skill
        assert "schema" in skill
        assert "share" in skill


def test_opensharing_all_skills(client: TestClient):
    """GET /opensharing/shares/company/skills should list all skills across schemas."""
    resp = client.get("/opensharing/shares/company/skills", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data


def test_opensharing_temporary_credentials(client: TestClient):
    """POST .../skills/{skill}/temporary-skill-credentials should return a download token."""
    # Find a skill to use
    zip_bytes = _make_zip()
    create_resp = client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "Cred Vending Skill",
            "description": "For credential vending test",
            "category": "general",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("cred.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["id"]

    resp = client.post(
        f"/opensharing/shares/company/schemas/general/skills/{asset_id}/temporary-skill-credentials",
        headers=ALICE,
    )
    assert resp.status_code == 200
    data = resp.json()
    # Must return a download token + expiry (+ optionally a presigned URL)
    assert "token" in data
    assert "expiry" in data
    assert "download_url" in data


def test_vended_download_url_works(client: TestClient):
    """The download_url vended by temporary-skill-credentials must be usable.

    Regression: the pre-signed (token-only) download flow was unreachable
    because the route required a Bearer token, so every vended download_url
    401'd.  The vended URL must download the payload without any Bearer header.
    """
    payload = "print('vended')"
    zip_bytes = _make_zip(payload)
    create_resp = client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "Vended Skill",
            "description": "Downloaded via vended token URL",
            "category": "general",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("vended.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    assert create_resp.status_code == 200
    asset_id = create_resp.json()["id"]

    cred = client.post(
        f"/opensharing/shares/company/schemas/general/skills/{asset_id}/temporary-skill-credentials",
        headers=ALICE,
    )
    assert cred.status_code == 200
    download_url = cred.json()["download_url"]

    # Fetch the vended URL with NO Authorization header — the signed token is the gate.
    resp = client.get(download_url)
    assert resp.status_code == 200, resp.text
    assert resp.content == zip_bytes


def test_download_no_credential_rejected(client: TestClient):
    """Downloading with neither a Bearer token nor a signed token must 401."""
    zip_bytes = _make_zip()
    create_resp = client.post(
        "/v1/assets",
        data={
            "kind": "skill",
            "name": "Gated Skill",
            "description": "Needs a credential",
            "category": "general",
            "visibility": "company",
            "version": "1.0",
        },
        files={"file": ("gated.zip", zip_bytes, "application/zip")},
        headers=ALICE,
    )
    asset_id = create_resp.json()["id"]

    resp = client.get(f"/v1/assets/{asset_id}/download")
    assert resp.status_code in (401, 403), resp.text


def test_opensharing_unknown_share_404(client: TestClient):
    """GET /opensharing/shares/{unknown}/schemas should return 404."""
    resp = client.get("/opensharing/shares/unknown-share/schemas", headers=ALICE)
    assert resp.status_code == 404


def test_opensharing_requires_auth(client: TestClient):
    """OpenSharing endpoints must require authentication."""
    resp = client.get("/opensharing/shares")
    assert resp.status_code in (401, 403)
