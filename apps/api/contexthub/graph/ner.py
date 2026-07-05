"""Deterministic NER for coding-session text — regex + gazetteer core, optional spaCy.

Why this exists: the only graph extractor today is the LLM (``extract.py``). That
is slow, costs tokens, is non-deterministic, and — critically — misses the
*structured code entities* that make cross-session linking work (services, repos,
the libraries a session touched). This module adds a cheap, deterministic pass
that runs BEFORE the LLM and feeds the same graph upsert path.

Design for a local-first OSS tool:
- **The core is pure regex + a curated tech gazetteer** — zero new dependencies,
  fully offline, always available. It covers the high cross-session-value kinds:
  ``service`` (``payments-api``), ``repo`` (``org/name``), ``tool`` (libraries /
  platforms from the gazetteer).
- **spaCy is an optional enhancement** (``contexthub[nlp]``). When installed it
  adds natural-language entities (``person``, ``org``, ``product``) via an
  ``EntityRuler`` (our code patterns) layered over the base ``en_core_web_sm``
  NER. When absent, everything still works — we just skip the NL entities.

Returned entities map onto the existing graph node kinds where possible so they
flow straight into ``GraphStore.upsert_node`` and the same_as resolver.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(frozen=True)
class Entity:
    kind: str
    name: str  # normalized (lowercased, trimmed)


# ---------------------------------------------------------------------------
# Curated tech gazetteer — high cross-session value ("which sessions used Redis?")
# Kept deliberately small and precise; extend as the corpus warrants.
# ---------------------------------------------------------------------------
_TECH_GAZETTEER = {
    # datastores / infra
    "postgres", "postgresql", "mysql", "sqlite", "redis", "mongodb", "lancedb",
    "elasticsearch", "kafka", "rabbitmq", "s3", "dynamodb", "kubernetes", "docker",
    "terraform", "nginx", "grpc",
    # languages / runtimes
    "python", "typescript", "javascript", "rust", "golang", "java", "ruby",
    # frameworks / libs
    "react", "vue", "svelte", "fastapi", "django", "flask", "express", "next.js",
    "pytest", "jest", "vitest", "tailwind", "pydantic", "sqlalchemy", "celery",
    # ai / ml
    "pytorch", "tensorflow", "huggingface", "openai", "anthropic", "langchain",
    "spacy", "minilm", "bert", "gliner", "flashrank",
    # vendors / services
    "stripe", "twilio", "sendgrid", "auth0", "okta", "github", "gitlab",
    "oauth", "jwt", "saml",
}

# Multi-word gazetteer phrases need their own pass (regex word-boundaries differ).
_TECH_PHRASES = {p for p in _TECH_GAZETTEER if " " in p or "." in p}
_TECH_WORDS = _TECH_GAZETTEER - _TECH_PHRASES

# service: foo-service, payments-api, auth-gateway, billing-worker …
_RE_SERVICE = re.compile(
    r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)*-(?:service|api|gateway|worker|daemon|server))\b",
    re.IGNORECASE,
)
# repo: ONLY from a github/gitlab URL context — a bare "x/y" in prose is far too
# often a path, a "request/response", "win/mac", etc. (verified noisy on real
# transcripts). Capturing the owner/repo from a real host avoids that garbage.
_RE_REPO = re.compile(
    r"(?:github|gitlab)\.com/([A-Za-z0-9][A-Za-z0-9._-]+/[A-Za-z0-9][A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
# file: a path ending in a known source/code extension
_RE_FILE = re.compile(
    r"\b([\w./-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|rb|json|ya?ml|toml|md|sql|sh|css|html))\b"
)
# function call: foo() / handle_event()
_RE_FUNC = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]{2,})\(\)")
# config / env constant: must contain an underscore to avoid SDK/API/CI noise
_RE_CONST = re.compile(r"\b([A-Z][A-Z0-9]*_[A-Z0-9_]+)\b")
# error type: SomethingError / SomethingException / ERR_FOO
_RE_ERROR = re.compile(r"\b((?:[A-Z][a-zA-Z0-9]*)?(?:Error|Exception)|ERR_[A-Z0-9_]+)\b")

# URLs, markdown link targets, and bare paths are where slug garbage comes from
# ("/get-started-with-caas-api" is a docs anchor, not a service).
_RE_URL = re.compile(r"(?:https?://|www\.)\S+|\]\([^)]*\)", re.IGNORECASE)

# GitHub/GitLab path prefixes that are site routes, not repositories.
_REPO_ROUTE_BLOCKLIST = {
    "login", "orgs", "settings", "search", "topics", "features", "blog",
    "about", "pulls", "issues", "notifications", "marketplace", "sponsors",
    "apps", "repos", "collections", "trending", "explore", "site", "contact",
}


def _strip_urls(text: str) -> str:
    """Blank out URLs / markdown link targets so slug fragments can't match."""
    return _RE_URL.sub(" ", text)


def _service_candidates(text: str) -> list[str]:
    """High-precision service names: not path segments, not header prefixes,
    and (unless code-quoted) mentioned more than once.

    A hyphenated token ending in api/server/gateway/… matched exactly once in
    plain prose is overwhelmingly a slug or a hypothetical, not a service the
    session actually touched (verified on the real corpus: turn-your-api,
    contracts-and-api, x-api). Requiring a second mention or backtick context
    keeps the real ones.
    """
    stripped = _strip_urls(text)
    raw: list[str] = []
    for m in _RE_SERVICE.finditer(stripped):
        start, end = m.start(1), m.end(1)
        before = stripped[start - 1] if start > 0 else " "
        after = stripped[end] if end < len(stripped) else " "
        if before == "/" or after in "/-":
            # path segment ("/reference/turn-your-api") or a longer hyphenated
            # token we only prefix-matched ("x-api-key")
            continue
        raw.append(m.group(1))

    counts: dict[str, int] = {}
    for name in raw:
        counts[name.lower()] = counts.get(name.lower(), 0) + 1

    low = text.lower()
    kept: list[str] = []
    for name in dict.fromkeys(n.lower() for n in raw):
        if counts[name] >= 2 or f"`{name}" in low:
            kept.append(name)
    return kept


_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)

# Never emit anything that looks like a credential — defense in depth so a token
# pasted into a transcript can't leak into the graph (or a shared hub) as a node.
_SECRET_RE = re.compile(
    r"(sk-[a-z0-9-]|xox[bapr]-|ghp_|gho_|github_pat_|AKIA[0-9A-Z]|AIza[0-9A-Za-z]|"
    r"-----BEGIN|eyJ[A-Za-z0-9_-]{10}|[a-f0-9]{32,})",
    re.IGNORECASE,
)
# Reject obvious non-entity noise: filesystem/temp paths and bare UUIDs that the
# regexes pick up from pasted paths / session ids.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", re.IGNORECASE)


def _norm(name: str) -> str:
    # Strip a leading article so spaCy spans like "the Stripe SDK" normalize to
    # "stripe sdk" rather than seeding a determiner-prefixed duplicate node.
    return _LEADING_ARTICLE.sub("", name.strip()).strip().lower()


def _is_noise(name: str) -> bool:
    if _SECRET_RE.search(name) or _UUID_RE.search(name):
        return True
    # path-ish fragments (temp dirs, encoded cwds) and over-long blobs
    if name.startswith((".", "/")) or "-users-" in name or "/private/" in name:
        return True
    if len(name) > 40 or name.count("/") > 1:
        return True
    return False


def _add(out: dict[tuple[str, str], Entity], kind: str, name: str) -> None:
    name = _norm(name)
    if not name or _is_noise(name):
        return
    out[(kind, name)] = Entity(kind=kind, name=name)


def extract_code_entities(text: str, granular: bool = False) -> list[Entity]:
    """Regex + gazetteer extraction — deterministic, offline, no deps.

    Default kinds (high cross-session value): ``service``, ``repo``, ``tool``.
    With ``granular=True`` also returns ``file``, ``function``, ``config``,
    ``error`` (more numerous, mostly session-local — opt in when you want them).
    """
    text = text or ""
    out: dict[tuple[str, str], Entity] = {}

    for name in _service_candidates(text):
        _add(out, "service", name)

    # repos: keep only plausible org/name (reject if it matched a file path)
    for m in _RE_REPO.finditer(text):
        cand = m.group(1)
        if "." in cand.split("/")[-1]:  # trailing segment has an extension → file
            continue
        if cand.split("/")[0].lower() in _REPO_ROUTE_BLOCKLIST:
            continue  # github.com/login/device, /orgs/…, /settings/… are routes
        _add(out, "repo", cand)

    low = text.lower()
    for word in _TECH_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", low):
            _add(out, "tool", word)
    for phrase in _TECH_PHRASES:
        if phrase in low:
            _add(out, "tool", phrase)

    if granular:
        for m in _RE_FILE.finditer(text):
            # The basename is the useful entity (a "store.py" node, not the whole
            # path) and stays clear of the path-noise filter.
            _add(out, "file", m.group(1).rsplit("/", 1)[-1])
        for m in _RE_FUNC.finditer(text):
            _add(out, "function", m.group(1))
        for m in _RE_CONST.finditer(text):
            _add(out, "config", m.group(1))
        for m in _RE_ERROR.finditer(text):
            _add(out, "error", m.group(1))

    return list(out.values())


# ---------------------------------------------------------------------------
# Optional spaCy enhancement
# ---------------------------------------------------------------------------

def spacy_available() -> bool:
    try:
        import spacy  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _get_nlp():
    """Load spaCy ``en_core_web_sm`` with an EntityRuler for our code patterns.

    Cached as a singleton (model load is ~100ms+). Returns None if spaCy or the
    model is unavailable, so callers degrade to the regex core.
    """
    try:
        import spacy
    except Exception:
        return None
    try:
        nlp = spacy.load("en_core_web_sm", disable=["lemmatizer"])
    except Exception:
        # Model not downloaded — fall back to a blank pipeline so the EntityRuler
        # code patterns still run even without the statistical NER.
        try:
            nlp = spacy.blank("en")
        except Exception:
            return None
    if "entity_ruler" not in nlp.pipe_names:
        ruler = nlp.add_pipe("entity_ruler", before="ner" if "ner" in nlp.pipe_names else None)
        patterns = [{"label": "TOOL", "pattern": w} for w in sorted(_TECH_WORDS)]
        patterns += [{"label": "TOOL", "pattern": p} for p in sorted(_TECH_PHRASES)]
        ruler.add_patterns(patterns)
    return nlp


# spaCy label → our graph node kind (only the NL kinds the regex core can't do).
_SPACY_KIND = {"PERSON": "person", "ORG": "org", "PRODUCT": "product", "TOOL": "tool"}


def extract_spacy_entities(text: str) -> list[Entity]:
    """Natural-language entities (person/org/product) via spaCy. [] if unavailable."""
    nlp = _get_nlp()
    if nlp is None or not text:
        return []
    out: dict[tuple[str, str], Entity] = {}
    try:
        doc = nlp(text[:100_000])  # cap to keep a pathological transcript bounded
    except Exception:
        return []
    for ent in doc.ents:
        kind = _SPACY_KIND.get(ent.label_)
        if kind:
            _add(out, kind, ent.text)
    return list(out.values())


def extract_entities(
    text: str,
    use_spacy: bool = True,
    granular: bool = False,
) -> list[Entity]:
    """Unified entity extraction: regex/gazetteer core + optional spaCy NL entities.

    Deduplicated by (kind, normalized-name). Always returns the deterministic core
    even when spaCy is absent, so the feature is fully functional offline.
    """
    merged: dict[tuple[str, str], Entity] = {}
    for e in extract_code_entities(text, granular=granular):
        merged[(e.kind, e.name)] = e
    if use_spacy:
        for e in extract_spacy_entities(text):
            merged.setdefault((e.kind, e.name), e)
    return list(merged.values())


# ---------------------------------------------------------------------------
# Graph integration — deterministic entity backbone for a session
# ---------------------------------------------------------------------------

_NER_INPUT_CHARS = 20_000  # NER is cheap, so we can scan much more than the LLM does
_MAX_ENTITIES_PER_SESSION = 25  # cap to bound the co-occurrence edge count

# High-precision kinds we trust into the shared graph. spaCy ORG/PRODUCT are
# noisy on technical prose ("the API", "JSON", "CI"), so they're deliberately
# excluded from the graph feed even though ``extract_entities`` still returns
# them for callers that want them.
# Clean, high-precision kinds for the shared graph. ``person`` is excluded by
# default: spaCy PERSON is too noisy on technical transcripts (verified ~90
# false positives across 60 real sessions). Callers can opt person back in.
_GRAPH_KINDS = {"service", "repo", "tool"}
_GRAPH_KINDS_GRANULAR = _GRAPH_KINDS | {"file", "function", "config", "error"}


def _session_text(session, summary: Optional[str]) -> str:
    parts: list[str] = []
    if summary and summary.strip():
        parts.append(summary.strip())
    total = sum(len(p) for p in parts)
    for msg in getattr(session, "messages", []) or []:
        t = (getattr(msg, "text", "") or "").strip()
        if not t:
            continue
        if total + len(t) > _NER_INPUT_CHARS:
            break
        parts.append(t)
        total += len(t)
    return "\n".join(parts)


def extract_ner_graph(
    session,
    summary: Optional[str],
    store,
    visibility: str = "company",
    author: Optional[str] = None,
    team: Optional[str] = None,
    use_spacy: bool = True,
    granular: bool = False,
) -> dict:
    """Deterministically extract entities for a session and upsert the backbone.

    Runs the NER core over summary + transcript and upserts each entity as a
    node with session provenance — the structural backbone the graph retrieval
    arm expands over. Cheap, offline, best-effort (never raises), and
    complementary to the LLM extractor. Co-occurrence edges are corpus-level
    (PPMI, see ``correlate.refresh_cooccur_edges``), not per-session stars.
    """
    try:
        text = _session_text(session, summary)
        if not text.strip():
            return {"nodes_upserted": 0, "edges_upserted": 0}
        ents = extract_entities(text, use_spacy=use_spacy, granular=granular)
        allowed = _GRAPH_KINDS_GRANULAR if granular else _GRAPH_KINDS
        ents = [e for e in ents if e.kind in allowed]
        if not ents:
            return {"nodes_upserted": 0, "edges_upserted": 0}
        ents = ents[:_MAX_ENTITIES_PER_SESSION]

        node_ids: list[str] = []
        for e in ents:
            try:
                nid = store.upsert_node(
                    kind=e.kind, name=e.name, session_id=session.id,
                    visibility=visibility, author=author, team=team,
                )
                node_ids.append(nid)
            except Exception:
                continue

        # Edges are NOT written per session: the old star topology (hub = first
        # matched entity) was semantically void. Corpus-level PPMI pairs are
        # rebuilt by contexthub.graph.correlate.refresh_cooccur_edges after a
        # build/backfill pass instead.
        return {"nodes_upserted": len(node_ids), "edges_upserted": 0}
    except Exception:
        return {"nodes_upserted": 0, "edges_upserted": 0}
