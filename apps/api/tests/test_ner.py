"""Tests for deterministic NER entity extraction (Slice S5).

The regex + gazetteer core is dependency-free and must work identically whether
or not spaCy is installed — those tests always run. The spaCy-specific test is
skipped when the optional ``[nlp]`` extra is absent, so CI stays green offline.
"""

from __future__ import annotations

import pytest

from contexthub.graph.ner import (
    Entity,
    extract_code_entities,
    extract_entities,
    spacy_available,
)


def _names(ents: list[Entity], kind: str) -> set[str]:
    return {e.name for e in ents if e.kind == kind}


def test_service_extraction():
    ents = extract_code_entities("Built the payments-api and the auth-gateway worker.")
    services = _names(ents, "service")
    assert "payments-api" in services
    assert "auth-gateway" in services


def test_repo_only_from_host_url():
    # A bare "x/y" in prose is NOT a repo (too noisy on real transcripts); only a
    # real github.com/gitlab.com URL yields a repo node.
    bare = extract_code_entities("Pushed to org/payments and request/response handling.")
    assert _names(bare, "repo") == set()
    url = extract_code_entities("Cloned https://github.com/acme/payments-api today.")
    assert "acme/payments-api" in _names(url, "repo")


def test_file_disambiguation():
    ents = extract_code_entities("Edited contexthub/graph/store.py in the repo.", granular=True)
    assert "contexthub/graph/store.py" not in _names(ents, "repo")
    assert any(f.endswith("store.py") for f in _names(ents, "file"))


def test_secrets_and_noise_never_emitted():
    # Token-like and path/uuid fragments must never become graph nodes.
    from contexthub.graph.ner import extract_entities
    txt = ("export OPENAI_API_KEY=sk-abc123def456 and a slack xoxb-99-token; "
           "session 8fd2d404-f3d0-4fb6-9620-a788f60e18e2 under /private/tmp/-users-foo.")
    names = {e.name for e in extract_entities(txt, use_spacy=False, granular=True)}
    assert not any("sk-abc" in n or "xoxb" in n for n in names)
    assert not any("8fd2d404" in n for n in names)
    assert not any(n.startswith(("/", ".")) or "-users-" in n for n in names)


def test_gazetteer_tools():
    ents = extract_code_entities("Cached in redis, queried postgres, embedded with MiniLM via LanceDB.")
    tools = _names(ents, "tool")
    assert {"redis", "postgres", "minilm", "lancedb"} <= tools


def test_granular_kinds_opt_in():
    text = "Reads DATABASE_URL, calls process_charge(), raised a RetryError."
    base = extract_code_entities(text, granular=False)
    gran = extract_code_entities(text, granular=True)
    # The granular kinds appear only when opted in.
    assert not any(e.kind in {"config", "function", "error"} for e in base)
    assert "database_url" in _names(gran, "config")
    assert "process_charge" in _names(gran, "function")
    assert "retryerror" in _names(gran, "error")


def test_const_requires_underscore_to_avoid_noise():
    # Bare acronyms (SDK, API, CI) must NOT be picked up as config constants.
    ents = extract_code_entities("Used the SDK and the API in CI.", granular=True)
    assert _names(ents, "config") == set()


def test_extract_entities_dedups_and_normalizes():
    ents = extract_entities("Redis and redis and REDIS in the payments-api.", use_spacy=False)
    # Case-folded dedup → a single 'redis' tool node.
    assert sum(1 for e in ents if e.kind == "tool" and e.name == "redis") == 1


def test_leading_article_stripped():
    from contexthub.graph.ner import _norm
    assert _norm("the Stripe SDK") == "stripe sdk"
    assert _norm("An OAuth flow") == "oauth flow"


@pytest.mark.skipif(not spacy_available(), reason="spaCy optional extra not installed")
def test_spacy_person_entities():
    from contexthub.graph.ner import extract_spacy_entities
    ents = extract_spacy_entities("Alice and Bob reviewed the change together in Berlin.")
    persons = _names(ents, "person")
    # At least one of the obvious person names should be detected.
    assert persons & {"alice", "bob"}


def test_extract_ner_graph_builds_backbone():
    """The NER pass upserts structural entities + co-occurrence edges into a graph,
    and excludes the noisy spaCy ORG/PRODUCT kinds from the shared graph feed."""
    import os, tempfile
    from contexthub.graph.store import GraphStore
    from contexthub.graph.ner import extract_ner_graph
    from contexthub.models import NormalizedSession, Message

    with tempfile.TemporaryDirectory() as tmp:
        store = GraphStore(os.path.join(tmp, "graph.db"))
        sess = NormalizedSession(
            id="s1", tool="claude-code", title="t", message_count=2,
            messages=[
                Message(id="m1", role="assistant",
                        text="Wired the payments-api to Stripe and cached in redis."),
            ],
        )
        res = extract_ner_graph(sess, summary="Worked on the payments-api service.", store=store)
        assert res["nodes_upserted"] >= 2
        names = {n["name"]: n["kind"] for n in store.list_nodes()}
        assert names.get("payments-api") == "service"
        assert "stripe" in names and "redis" in names
        # Co-occurrence edges connect the session's entities.
        assert any(e["rel"] == "co_occurs" for e in store.list_edges())
        # All node kinds are from the high-precision allowlist.
        assert set(names.values()) <= {"service", "repo", "tool", "person"}
