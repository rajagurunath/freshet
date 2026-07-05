"""Unit tests for the offline graph build helpers (worthiness + LLM input)."""

import os
import time


def _parsed(path: str, messages: list[tuple[str, str]], tool: str = "claude-code") -> dict:
    return {"messages": messages, "cwd": "/tmp/proj", "ts": None, "tool": tool, "path": path}


def test_session_worthiness_prefers_rich_recent_sessions(tmp_path):
    from contexthub.graph.build import session_worthiness

    rich = tmp_path / "rich.jsonl"
    rich.write_text("x")
    poor = tmp_path / "poor.jsonl"
    poor.write_text("x")
    old = time.time() - 300 * 86400
    os.utime(poor, (old, old))

    rich_parsed = _parsed(str(rich), [("user", "long prompt " * 100), ("assistant", "a" * 500)] * 10)
    poor_parsed = _parsed(str(poor), [("user", "hi")])

    assert session_worthiness(rich_parsed) > session_worthiness(poor_parsed)
    assert 0.0 <= session_worthiness(poor_parsed) <= 1.0
    assert 0.0 <= session_worthiness(rich_parsed) <= 1.0


def test_llm_input_includes_title_ask_and_outcome(tmp_path):
    from contexthub.graph.build import llm_input_for

    p = _parsed(str(tmp_path / "s.jsonl"), [
        ("user", "Fix the checkout race condition\nmore detail here"),
        ("assistant", "intermediate"),
        ("assistant", "Root cause was a stale session id; fixed in store.py"),
    ])
    text = llm_input_for(p)
    assert text.startswith("Title: Fix the checkout race condition")
    assert "checkout race condition" in text
    assert "stale session id" in text  # LAST assistant message, not the first


def test_llm_extract_tracking_roundtrip(tmp_path):
    from contexthub.graph.store import GraphStore

    store = GraphStore(str(tmp_path / "g.db"))
    assert store.llm_extracted_session_ids() == set()
    store.mark_llm_extracted("s1")
    store.mark_llm_extracted("s1")  # idempotent
    assert store.llm_extracted_session_ids() == {"s1"}
