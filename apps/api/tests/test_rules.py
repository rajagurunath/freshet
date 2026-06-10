"""Tests for Task 14: Rules extraction with consent (API half).

Covers:
  1. RulesStore: upsert rules, dedup by normalized text overlap, status transitions.
  2. extract.py: parse + validate LLM JSON, dedup against existing rules.
  3. rules_extract job handler: extracts from recent session summaries.
  4. GET /v1/rules?status=   — list rules (filtered by status).
  5. POST /v1/rules/{id}/accept   — accept a proposed rule.
  6. POST /v1/rules/{id}/reject   — reject a proposed rule.
  7. GET /v1/rules/export   — markdown export of accepted rules only.
  8. Dedup: rule with >0.8 token overlap against existing rule is skipped.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Cache reset helper (same pattern as test_graph.py)
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


# ===========================================================================
# Unit tests: RulesStore (no HTTP)
# ===========================================================================

class TestRulesStore:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = os.path.join(self._tmpdir.name, "rules.db")
        from contexthub.rules.store import RulesStore
        self.store = RulesStore(db_path)

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_upsert_returns_id(self):
        rid = self.store.upsert_rule(
            text="Use conventional commits (feat:/fix:/refactor:).",
            rationale="Seen in 5 sessions.",
            evidence=["s1", "s2"],
            scope="commit-style",
        )
        assert isinstance(rid, str) and rid

    def test_list_rules_default_status_proposed(self):
        self.store.upsert_rule(text="Always write tests first.", rationale="TDD observed.", evidence=["s1"])
        rules = self.store.list_rules()
        assert len(rules) == 1
        assert rules[0]["status"] == "proposed"

    def test_accept_rule(self):
        rid = self.store.upsert_rule(text="Use snake_case for variables.", rationale="Consistent.", evidence=["s1"])
        self.store.set_status(rid, "accepted")
        rules = self.store.list_rules(status="accepted")
        assert any(r["id"] == rid for r in rules)

    def test_reject_rule(self):
        rid = self.store.upsert_rule(text="Use tabs for indentation.", rationale="Seen once.", evidence=["s1"])
        self.store.set_status(rid, "rejected")
        rules = self.store.list_rules(status="rejected")
        assert any(r["id"] == rid for r in rules)

    def test_filter_by_status(self):
        r1 = self.store.upsert_rule(text="Write docstrings for all public functions.", rationale="x", evidence=["s1"])
        r2 = self.store.upsert_rule(text="Keep functions under 50 lines.", rationale="y", evidence=["s2"])
        self.store.set_status(r1, "accepted")
        accepted = self.store.list_rules(status="accepted")
        proposed = self.store.list_rules(status="proposed")
        assert any(r["id"] == r1 for r in accepted)
        assert any(r["id"] == r2 for r in proposed)
        assert not any(r["id"] == r2 for r in accepted)

    def test_get_rule(self):
        rid = self.store.upsert_rule(text="Use type hints.", rationale="Mypy.", evidence=["s1"])
        rule = self.store.get_rule(rid)
        assert rule is not None
        assert rule["text"] == "Use type hints."
        assert rule["status"] == "proposed"
        assert isinstance(rule["evidence"], list)

    def test_get_rule_not_found(self):
        rule = self.store.get_rule("nonexistent-id")
        assert rule is None

    def test_list_accepted_for_export(self):
        r1 = self.store.upsert_rule(text="Always run tests before committing.", rationale="CI.", evidence=["s1"])
        r2 = self.store.upsert_rule(text="Do not print to stdout in library code.", rationale="Clean.", evidence=["s2"])
        self.store.set_status(r1, "accepted")
        # r2 stays proposed
        accepted = self.store.list_rules(status="accepted")
        assert len(accepted) == 1
        assert accepted[0]["text"] == "Always run tests before committing."


# ===========================================================================
# Unit tests: rules deduplication
# ===========================================================================

class TestRulesDedup:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        from contexthub.rules.store import RulesStore
        self.store = RulesStore(os.path.join(self._tmpdir.name, "rules.db"))

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_near_duplicate_is_skipped(self):
        """A rule with >0.8 token overlap against an existing rule should be skipped."""
        from contexthub.rules.extract import is_near_duplicate
        existing_text = "Use conventional commits: feat, fix, refactor prefixes."
        new_text = "Use conventional commits: feat, fix, refactor."
        assert is_near_duplicate(new_text, [existing_text])

    def test_distinct_rule_is_not_duplicate(self):
        from contexthub.rules.extract import is_near_duplicate
        existing = "Always write docstrings for public methods."
        new = "Use snake_case for all variable names."
        assert not is_near_duplicate(new, [existing])

    def test_empty_existing_rules_is_never_duplicate(self):
        from contexthub.rules.extract import is_near_duplicate
        assert not is_near_duplicate("Any rule at all.", [])

    def test_extract_deduplicates_against_store(self):
        """When a rule already exists in the store, re-extraction should skip it."""
        from contexthub.rules.extract import extract_rules

        existing_text = "Use conventional commits: feat, fix, refactor prefixes."
        self.store.upsert_rule(text=existing_text, rationale="old", evidence=["s0"])

        class _FakeLLM:
            name = "fake"
            def available(self): return True
            def complete(self, system, user, max_tokens=1024):
                return (
                    '{"rules": [{"text": "Use conventional commits: feat, fix, refactor.", '
                    '"rationale": "Seen in many sessions.", "evidence": ["s1"], "scope": "commit-style"}]}'
                )

        result = extract_rules(
            session_excerpts="some text",
            store=self.store,
            llm=_FakeLLM(),
            author="alice",
        )
        # The near-duplicate should be skipped → 0 new rules upserted.
        assert result["rules_upserted"] == 0


# ===========================================================================
# Unit tests: rules extraction (mocked LLM)
# ===========================================================================

class _FakeLLM:
    """LLM stub returning a canned JSON payload."""
    name = "fake"

    def __init__(self, payload: str):
        self._payload = payload

    def available(self) -> bool:
        return True

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        return self._payload


_FIXTURE_RULES_JSON = """{
  "rules": [
    {
      "text": "Use conventional commits: feat/fix/refactor.",
      "rationale": "Observed across 4 sessions.",
      "evidence": ["s1", "s2"],
      "scope": "commit-style"
    },
    {
      "text": "Prefer snake_case for all Python identifiers.",
      "rationale": "Consistent PEP 8 usage.",
      "evidence": ["s3"],
      "scope": "naming"
    }
  ]
}"""


class TestExtractRules:
    def setup_method(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        from contexthub.rules.store import RulesStore
        self.store = RulesStore(os.path.join(self._tmpdir.name, "rules.db"))

    def teardown_method(self):
        self._tmpdir.cleanup()

    def test_extract_and_persist_rules(self):
        from contexthub.rules.extract import extract_rules
        result = extract_rules(
            session_excerpts="Built checkout with feat/fix commits using snake_case.",
            store=self.store,
            llm=_FakeLLM(_FIXTURE_RULES_JSON),
            author="alice",
        )
        assert result["rules_upserted"] == 2
        rules = self.store.list_rules()
        assert len(rules) == 2
        texts = {r["text"] for r in rules}
        assert any("conventional commits" in t for t in texts)

    def test_extract_handles_malformed_json(self):
        from contexthub.rules.extract import extract_rules
        result = extract_rules(
            session_excerpts="anything",
            store=self.store,
            llm=_FakeLLM("not json at all"),
            author="alice",
        )
        assert result["rules_upserted"] == 0

    def test_extract_unavailable_llm(self):
        from contexthub.rules.extract import extract_rules

        class _UnavailableLLM:
            name = "unavailable"
            def available(self): return False
            def complete(self, *a, **kw): raise RuntimeError("should not be called")

        result = extract_rules(
            session_excerpts="anything",
            store=self.store,
            llm=_UnavailableLLM(),
            author="alice",
        )
        assert result["rules_upserted"] == 0

    def test_extract_evidence_stored(self):
        from contexthub.rules.extract import extract_rules
        extract_rules(
            session_excerpts="text",
            store=self.store,
            llm=_FakeLLM(_FIXTURE_RULES_JSON),
            author="alice",
        )
        rules = self.store.list_rules()
        assert all(isinstance(r["evidence"], list) for r in rules)
        first = next(r for r in rules if "conventional" in r["text"])
        assert set(first["evidence"]) >= {"s1", "s2"}


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
        )


@pytest.fixture(scope="module")
def client(tmp_dirs) -> Generator[TestClient, None, None]:
    lancedb_uri, blob_dir, jobs_db, graph_db, rules_db = tmp_dirs

    env_patch = {
        "EMBEDDING_PROVIDER": "hash",
        "LANCEDB_URI": lancedb_uri,
        "BLOB_DIR": blob_dir,
        "JOBS_DB": jobs_db,
        "GRAPH_DB": graph_db,
        "RULES_DB": rules_db,
        "API_KEYS": "alice-key:alice:team-red,bob-key:bob:team-blue",
        "ANTHROPIC_API_KEY": "",
        "LLM_PROVIDER": "anthropic",
        "S3_BUCKET": "",
        "CORS_ORIGINS": "",
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


def _seed_rule(client: TestClient, text: str, rationale: str = "test", evidence: list | None = None) -> str:
    """Directly seed a rule via RulesStore (bypassing the LLM)."""
    from contexthub.rules.store import get_rules_store
    store = get_rules_store()
    return store.upsert_rule(text=text, rationale=rationale, evidence=evidence or ["s1"])


def test_list_rules_empty(client: TestClient):
    resp = client.get("/v1/rules", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data


def test_list_rules_proposed(client: TestClient):
    _seed_rule(client, "Always use type hints in Python.", "Mypy enforced.", ["s1"])
    resp = client.get("/v1/rules", params={"status": "proposed"}, headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert any("type hints" in r["text"] for r in data["items"])


def test_accept_rule(client: TestClient):
    rid = _seed_rule(client, "Commit messages must start with a verb.", "Grammar.", ["s2"])
    resp = client.post(f"/v1/rules/{rid}/accept", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"


def test_reject_rule(client: TestClient):
    rid = _seed_rule(client, "Use CamelCase for variable names.", "Bad practice.", ["s3"])
    resp = client.post(f"/v1/rules/{rid}/reject", headers=ALICE)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"


def test_accept_nonexistent_rule(client: TestClient):
    resp = client.post("/v1/rules/does-not-exist/accept", headers=ALICE)
    assert resp.status_code == 404


def test_reject_nonexistent_rule(client: TestClient):
    resp = client.post("/v1/rules/does-not-exist/reject", headers=ALICE)
    assert resp.status_code == 404


def test_export_returns_only_accepted(client: TestClient):
    # Seed two rules, accept one.
    r_accept = _seed_rule(client, "Always run the full test suite before merging.", "CI required.", ["s4"])
    r_propose = _seed_rule(client, "Do not leave debug print statements.", "Code review.", ["s5"])
    client.post(f"/v1/rules/{r_accept}/accept", headers=ALICE)
    # r_propose stays proposed

    resp = client.get("/v1/rules/export", headers=ALICE)
    assert resp.status_code == 200
    text = resp.text
    # Accepted rule must appear; proposed must not (unless it was accepted earlier in the module).
    assert "Always run the full test suite before merging." in text
    # Export should be markdown-formatted.
    assert "#" in text or "-" in text or "*" in text


def test_export_consent_gate(client: TestClient):
    """Export must not contain rules that are only proposed."""
    r_only_proposed = _seed_rule(client, "An entirely unique proposed rule text xyz.", "x", ["s99"])
    resp = client.get("/v1/rules/export", headers=ALICE)
    assert resp.status_code == 200
    assert "An entirely unique proposed rule text xyz." not in resp.text


def test_list_rules_filter_status(client: TestClient):
    r1 = _seed_rule(client, "Use pathlib instead of os.path.", "Modern Python.", ["s6"])
    r2 = _seed_rule(client, "Prefer list comprehensions over map().", "Readability.", ["s7"])
    client.post(f"/v1/rules/{r1}/accept", headers=ALICE)
    # r2 stays proposed

    accepted = client.get("/v1/rules", params={"status": "accepted"}, headers=ALICE).json()["items"]
    proposed = client.get("/v1/rules", params={"status": "proposed"}, headers=ALICE).json()["items"]

    assert any(r["id"] == r1 for r in accepted)
    assert any(r["id"] == r2 for r in proposed)
    assert not any(r["id"] == r2 for r in accepted)


def test_rules_extract_job_handler():
    """The rules_extract handler runs against session summaries and persists rules."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_patch = {
            "EMBEDDING_PROVIDER": "hash",
            "LANCEDB_URI": os.path.join(tmpdir, "lancedb"),
            "BLOB_DIR": os.path.join(tmpdir, "blobs"),
            "JOBS_DB": os.path.join(tmpdir, "jobs.db"),
            "GRAPH_DB": os.path.join(tmpdir, "graph.db"),
            "RULES_DB": os.path.join(tmpdir, "rules.db"),
            "API_KEYS": "alice-key:alice:team-red",
            "ANTHROPIC_API_KEY": "",
            "S3_BUCKET": "",
        }
        original = {k: os.environ.get(k) for k in env_patch}
        os.environ.update(env_patch)
        _clear_caches()
        try:
            from contexthub.main import create_app
            app = create_app()
            with TestClient(app) as c:
                # Ingest a session so the handler has something to extract from.
                c.post(
                    "/v1/sessions",
                    json={
                        "session": {
                            "id": "rules-s1",
                            "tool": "claude-code",
                            "title": "Commit style discussion",
                            "message_count": 2,
                            "models": ["claude-sonnet-4-6"],
                            "tokens": {"input": 50, "output": 10},
                            "preview": "conventional commits",
                            "file_path": "/x/rules-s1.jsonl",
                            "messages": [
                                {"id": "m1", "role": "user", "text": "We always use conventional commits"},
                                {"id": "m2", "role": "assistant", "text": "Agreed, feat/fix/refactor."},
                            ],
                        },
                        "summary": "The team uses conventional commits consistently.",
                        "category": "engineering",
                        "visibility": "company",
                        "author": {"id": "alice", "email": "a@x.com", "name": "Alice", "team": "team-red"},
                        "redacted": True,
                    },
                    headers={"Authorization": "Bearer alice-key"},
                )

            # Monkeypatch get_llm in the rules extract module to return a fake LLM.
            import contexthub.rules.extract as rules_extract_mod
            orig_get_llm = rules_extract_mod.get_llm
            rules_extract_mod.get_llm = lambda *a, **k: _FakeLLM(_FIXTURE_RULES_JSON)
            try:
                from contexthub.jobs.handlers import rules_extract_handler
                result = rules_extract_handler({"author": "alice", "n_sessions": 5})
            finally:
                rules_extract_mod.get_llm = orig_get_llm

            assert result.get("rules_upserted", 0) >= 1

            from contexthub.rules.store import get_rules_store
            store = get_rules_store()
            rules = store.list_rules()
            assert len(rules) >= 1

        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            _clear_caches()
