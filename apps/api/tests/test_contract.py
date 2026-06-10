"""Contract parity tests.

The shared desktop<->API contract is defined by the Pydantic models in
`contexthub.models` and exported to `apps/api/schema/contract.json` by
`scripts/export_schema.py`. The desktop generates `contract.gen.ts` from that
JSON. These tests fail whenever the models change without re-exporting the
schema, which is the drift guard.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = API_ROOT / "schema" / "contract.json"
EXPORT_SCRIPT = API_ROOT / "scripts" / "export_schema.py"


def _load_export_module():
    spec = importlib.util.spec_from_file_location("export_schema", EXPORT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_checked_in_contract_matches_fresh_export():
    """`schema/contract.json` must match a fresh export of the models.

    If this fails: run `python scripts/export_schema.py` in apps/api and
    `npm run gen:types` in apps/desktop, then commit the regenerated files.
    """
    mod = _load_export_module()
    fresh = mod.build_contract()
    assert SCHEMA_PATH.exists(), (
        "schema/contract.json missing — run python scripts/export_schema.py"
    )
    checked_in = json.loads(SCHEMA_PATH.read_text())
    assert checked_in == fresh, (
        "schema/contract.json is stale — run python scripts/export_schema.py "
        "and npm run gen:types, then commit"
    )


def test_contract_contains_core_models():
    mod = _load_export_module()
    defs = mod.build_contract()["$defs"]
    for name in (
        "NormalizedSession",
        "Message",
        "TokenCounts",
        "Author",
        "SessionLink",
        "IngestRequest",
        "IngestResponse",
        "QueryRequest",
        "QueryResponse",
        "SummarizeRequest",
        "SummarizeResponse",
        "SessionCatalogRow",
        "StatsResponse",
    ):
        assert name in defs, f"missing model in contract: {name}"


def test_normalized_session_v2_fields():
    """The v2 session fields exist with the right defaults/shapes."""
    from contexthub.models import NormalizedSession, SessionLink

    schema = NormalizedSession.model_json_schema()
    props = schema["properties"]

    assert props["schema_version"]["default"] == 2
    assert props["compacted"]["default"] is False
    for field in ("compact_summary", "parent_session_id", "branch_point_message_id"):
        assert field in props, f"missing field: {field}"
    assert "links" in props

    # SessionLink shape
    link_schema = SessionLink.model_json_schema()
    assert set(link_schema["properties"]["kind"]["enum"]) == {
        "pr",
        "issue",
        "doc",
        "session",
    }
    assert "url" in link_schema["required"]

    # Defaults behave: a minimal session is schema_version 2, not compacted.
    s = NormalizedSession(id="x", tool="claude-code", title="t")
    assert s.schema_version == 2
    assert s.compacted is False
    assert s.compact_summary is None
    assert s.parent_session_id is None
    assert s.branch_point_message_id is None
    assert s.links == []

    # Links round-trip through the wire format.
    s2 = NormalizedSession(
        id="y",
        tool="claude-code",
        title="t",
        compacted=True,
        compact_summary="condensed",
        parent_session_id="x",
        branch_point_message_id="m3",
        links=[{"kind": "pr", "url": "https://github.com/o/r/pull/1", "label": "PR #1"}],
    )
    dumped = s2.model_dump()
    assert dumped["links"][0]["kind"] == "pr"
    assert dumped["compact_summary"] == "condensed"
