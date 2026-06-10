"""LLM-driven rules extraction (Task 14).

Given excerpts from the author's recent session summaries/transcripts, ask the LLM
to return a strict JSON object::

    {
      "rules": [
        {
          "text": "<one-line actionable rule>",
          "rationale": "<one-line why>",
          "evidence": ["session-id-1", "session-id-2"],
          "scope": "<optional label, e.g. commit-style>"
        }
      ]
    }

We validate, dedup against existing rules (lowercased token overlap > 0.8 → skip),
and upsert into RulesStore in 'proposed' status.

Extraction is best-effort: malformed JSON or an unavailable LLM yields an empty
result rather than raising, so it never blocks the job queue.

Consent gate
------------
All extracted rules start as 'proposed'.  Nothing is exported until the user
explicitly accepts each rule via POST /v1/rules/{id}/accept.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from contexthub.llm import LLMError, get_llm  # module-level so tests can monkey-patch

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You extract recurring user preferences and conventions from software session excerpts.

Return ONLY a JSON object with this exact shape (no prose, no markdown fence):
{
  "rules": [
    {
      "text": "<one short actionable sentence — the rule itself>",
      "rationale": "<one line: why this rule, what evidence supports it>",
      "evidence": ["<session_id_1>", "<session_id_2>"],
      "scope": "<optional short label: commit-style | naming | testing | tooling | review | other>"
    }
  ]
}

Identify only *recurring* preferences observed across multiple sessions or mentioned
explicitly as a team convention.  Do not invent rules.  Each rule should be
actionable and specific.  Keep text ≤ 120 characters.  At most 10 rules.

If no clear recurring preferences are found, return {"rules": []}.
"""


def _tokenize(text: str) -> set[str]:
    """Return a set of lowercase word tokens from text (for overlap comparison)."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def is_near_duplicate(new_text: str, existing_texts: list[str], threshold: float = 0.8) -> bool:
    """Return True if ``new_text`` has token overlap > threshold with any existing text.

    Uses Jaccard similarity on lowercase word tokens.  This is intentionally
    simple and fast — no embeddings required.
    """
    new_tokens = _tokenize(new_text)
    if not new_tokens:
        return False
    for existing in existing_texts:
        existing_tokens = _tokenize(existing)
        if not existing_tokens:
            continue
        intersection = len(new_tokens & existing_tokens)
        union = len(new_tokens | existing_tokens)
        if union > 0 and (intersection / union) > threshold:
            return True
    return False


def _parse_json(raw: str) -> Optional[dict[str, Any]]:
    """Best-effort parse of an LLM response into the expected dict.

    Tolerates code fences and leading/trailing prose by extracting the first
    balanced JSON object.
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip a ```json ... ``` fence if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (ValueError, TypeError):
        pass
    # Fallback: grab the outermost {...}.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            return None
    return None


def extract_rules(
    session_excerpts: str,
    store: Any,
    llm: Optional[Any] = None,
    settings: Optional[Any] = None,
    author: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Extract rules from session excerpts and upsert into the store.

    Args:
        session_excerpts: Concatenated summary/transcript text from recent sessions.
        store:            A RulesStore instance.
        llm:              Optional pre-built LLM client (tests inject a stub).
                          When None, built from ``settings`` / provider / model.
        author:           User id whose sessions were mined (stored as provenance).

    Returns:
        {"rules_upserted": int, "rules_skipped_duplicate": int}
        Best-effort: never raises on bad LLM output.
    """
    if not session_excerpts or not session_excerpts.strip():
        return {"rules_upserted": 0, "rules_skipped_duplicate": 0}

    if llm is None:
        llm = get_llm(settings, provider_override=provider, model_override=model)

    try:
        if not llm.available():
            logger.info("rules extract: LLM unavailable")
            return {"rules_upserted": 0, "rules_skipped_duplicate": 0}
        raw = llm.complete(_SYSTEM_PROMPT, f"Session excerpts:\n{session_excerpts}", max_tokens=2048)
    except Exception as exc:
        logger.warning("rules extract: LLM call failed: %s", exc)
        return {"rules_upserted": 0, "rules_skipped_duplicate": 0}

    obj = _parse_json(raw)
    if not obj:
        logger.warning("rules extract: could not parse JSON from LLM response")
        return {"rules_upserted": 0, "rules_skipped_duplicate": 0}

    raw_rules = obj.get("rules") or []
    if not isinstance(raw_rules, list):
        return {"rules_upserted": 0, "rules_skipped_duplicate": 0}

    # Load existing rule texts for dedup.
    existing_texts: list[str] = store.list_all_texts()

    rules_upserted = 0
    rules_skipped = 0

    for r in raw_rules:
        if not isinstance(r, dict):
            continue
        text = str(r.get("text") or "").strip()
        if not text:
            continue
        # Dedup: skip if near-duplicate of any existing rule.
        if is_near_duplicate(text, existing_texts):
            logger.debug("rules extract: skipping near-duplicate rule: %.60s", text)
            rules_skipped += 1
            continue

        rationale = str(r.get("rationale") or "").strip() or None
        evidence = r.get("evidence")
        if not isinstance(evidence, list):
            evidence = []
        evidence = [str(e) for e in evidence if e]
        scope = str(r.get("scope") or "").strip() or None

        try:
            store.upsert_rule(
                text=text,
                rationale=rationale,
                evidence=evidence,
                scope=scope,
                author=author,
            )
            existing_texts.append(text)  # Update in-memory list to dedup within this batch.
            rules_upserted += 1
        except Exception as exc:
            logger.warning("rules extract: failed to upsert rule '%.40s': %s", text, exc)

    logger.info("rules extract: %d upserted, %d skipped (duplicate)", rules_upserted, rules_skipped)
    return {"rules_upserted": rules_upserted, "rules_skipped_duplicate": rules_skipped}
