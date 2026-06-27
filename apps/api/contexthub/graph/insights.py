"""Cross-session insights derived from the knowledge graph.

A lightweight, deterministic "what does this person/team keep doing?" layer over
the union graph — the fest.md #10 ask ("extract the small common things across all
sessions"), answered structurally rather than by asking an LLM to guess.

- ``stack_profile`` ranks the entities (tools/services/repos) that recur across
  the most sessions — the standard stack, straight from node→session provenance.
- ``common_pairings`` reuses the PPMI signal (``correlate.ppmi_entity_pairs``) to
  surface the tool/service *combinations* that co-occur far more than chance.

These feed a "your standard stack / common patterns" view and can seed
rule-proposal candidates (e.g. "you use pytest in 7/10 sessions") with evidence
counts — cheap, explainable, offline, and visibility-enforced via the store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class StackEntry:
    kind: str
    name: str
    sessions: int  # how many distinct sessions reference this entity


def stack_profile(
    store: Any,
    kinds: Optional[set[str]] = None,
    min_sessions: int = 2,
    limit: int = 25,
    caller_user_id: Optional[str] = None,
    caller_team: Optional[str] = None,
) -> list[StackEntry]:
    """Rank recurring entities by the number of sessions that reference them.

    Args:
        kinds: restrict to these node kinds (default: structural stack kinds —
            tool/service/repo/library). Pass None-equivalent {} only via explicit set.
        min_sessions: ignore entities that appear in fewer sessions (one-offs).
        limit: cap the returned list.
    """
    if kinds is None:
        kinds = {"tool", "service", "repo", "library"}
    try:
        nodes = store.list_nodes(caller_user_id=caller_user_id, caller_team=caller_team)
    except TypeError:
        nodes = store.list_nodes()

    out: list[StackEntry] = []
    for n in nodes:
        if kinds and n.get("kind") not in kinds:
            continue
        try:
            count = len(store.sessions_for_node(n["id"]))
        except Exception:
            count = 0
        if count >= min_sessions:
            out.append(StackEntry(kind=n["kind"], name=n["name"], sessions=count))

    out.sort(key=lambda e: (e.sessions, e.name), reverse=True)
    return out[:limit]


def common_pairings(
    store: Any,
    min_cooccur: int = 2,
    limit: int = 25,
    caller_user_id: Optional[str] = None,
    caller_team: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Top entity *combinations* by PPMI — the patterns that recur together.

    Thin wrapper over ``correlate.ppmi_entity_pairs`` shaped for display.
    """
    from contexthub.graph.correlate import ppmi_entity_pairs

    pairs = ppmi_entity_pairs(
        store, min_cooccur=min_cooccur,
        caller_user_id=caller_user_id, caller_team=caller_team,
    )
    return [
        {"a": p.a, "b": p.b, "ppmi": p.ppmi, "cooccur": p.cooccur}
        for p in pairs[:limit]
    ]
