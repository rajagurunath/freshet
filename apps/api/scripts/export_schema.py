#!/usr/bin/env python3
"""Export the shared desktop<->API contract as JSON Schema.

Dumps the Pydantic models that make up the wire contract to
`apps/api/schema/contract.json`. The desktop converts that file into
TypeScript types via `apps/desktop/scripts/gen-types.mjs`
(`npm run gen:types`).

Run from apps/api:  python scripts/export_schema.py
Parity is enforced by tests/test_contract.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = API_ROOT / "schema" / "contract.json"

# Make `contexthub` importable when run as a plain script.
sys.path.insert(0, str(API_ROOT))

CONTRACT_VERSION = 2


def build_contract() -> dict:
    """Build the full contract document (deterministic, sorted keys)."""
    from pydantic.json_schema import models_json_schema

    from contexthub.models import (
        IngestRequest,
        IngestResponse,
        QueryRequest,
        QueryResponse,
        SessionCatalogRow,
        StatsResponse,
        SummarizeRequest,
        SummarizeResponse,
    )

    models = [
        IngestRequest,
        IngestResponse,
        QueryRequest,
        QueryResponse,
        SummarizeRequest,
        SummarizeResponse,
        SessionCatalogRow,
        StatsResponse,
    ]
    _, top = models_json_schema(
        [(m, "validation") for m in models],
        ref_template="#/$defs/{model}",
    )
    # Round-trip through JSON to normalize types (tuples, etc.) and key order.
    defs = json.loads(json.dumps(top["$defs"], sort_keys=True))
    return {
        "contract_name": "contexthub",
        "contract_version": CONTRACT_VERSION,
        "$defs": defs,
    }


def main() -> None:
    contract = build_contract()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT_PATH} ({len(contract['$defs'])} models)")


if __name__ == "__main__":
    main()
