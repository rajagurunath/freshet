"""Deterministic synthetic multi-session corpus for retrieval evaluation.

The corpus is hand-authored as a set of *storylines* — a feature that spans
several sessions and teams (engineering, marketing, ops), naming recurring
entities (services, features, libraries) across sessions, sometimes under an
alias ("checkout" vs "payment-checkout"). That cross-session entity structure is
exactly what the knowledge graph is supposed to exploit, so it lets us measure
whether the graph actually helps retrieval beyond plain vector search.

To simulate the real failure mode — *a long session where the answer is buried
and keyword search misses it* — each session's transcript is padded with
deterministic plausible filler around a few planted "answer" sentences.

Everything is seeded/deterministic: no randomness, no network, no LLM. The
generator returns ``NormalizedSession`` objects plus the silver question set
whose gold labels are the source session ids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contexthub.models import Message, NormalizedSession

# ---------------------------------------------------------------------------
# Storyline data — the substance of the benchmark
# ---------------------------------------------------------------------------


@dataclass
class _Sess:
    sid: str
    tool: str
    team: str
    project: str
    summary: str
    # The few sentences that actually carry the answer (buried among filler).
    answer_lines: list[str]
    # Canonical entity names this session is "about" (graph provenance).
    entities: list[str]
    # Optional explicit title; default is a generic, non-leaking title so the
    # answer can't be retrieved straight from the catalog title.
    title: str = ""


@dataclass
class _Story:
    topic: str
    sessions: list[_Sess]
    questions: list[dict[str, Any]] = field(default_factory=list)


# Deterministic filler pool — plausible coding-session chatter that contains NO
# planted answer, so it lengthens sessions without leaking gold signal.
_FILLER = [
    "Let me start by reading through the existing modules to understand the layout.",
    "I ran the test suite and most things pass; a couple of warnings about deprecations.",
    "Reformatting the imports and tidying up the helper functions first.",
    "Looks like there is some dead code here we can remove later.",
    "I will add a docstring and a couple of inline comments for clarity.",
    "Checked the logs and nothing unusual stands out in the request traces.",
    "Let me wire up the new function and confirm the types line up.",
    "Running a quick lint pass to keep the style consistent with the repo.",
    "I refactored the loop into a comprehension to make it a bit clearer.",
    "Committing the work-in-progress so we have a checkpoint to return to.",
    "Pulled the latest changes and rebased onto the main branch cleanly.",
    "Adding a small unit test to cover the edge case we discussed.",
    "The CI run is green now after fixing the import ordering.",
    "I double-checked the config defaults and they look reasonable.",
    "Let me trace the call path once more to be sure nothing is missed.",
]


def _storylines() -> list[_Story]:
    """Storylines tuned for *honest difficulty*.

    Design principles so vanilla vector+FTS does NOT trivially win:
    - **Generic summaries** on bridge/alias targets — the discriminating detail
      lives only in the buried transcript, not the high-signal chunk-0 summary.
    - **Vocabulary mismatch** — questions are phrased as a human would ask, using
      synonyms the answer session never uses (brute-force vs credential-stuffing).
    - **Lexical decoy sessions** — same-topic sessions that share the query's words
      but are the *wrong* answer, so top-k is genuinely contended.
    - **Bridge questions** — the gold session shares NO query vocabulary; it is only
      reachable through a shared entity (the entity graph's reason to exist).
    """
    return [
        _Story(
            topic="checkout",
            sessions=[
                _Sess(
                    sid="s-checkout-impl",
                    tool="claude-code", team="eng", project="payments-api",
                    # Generic summary: says nothing about Stripe/idempotency.
                    summary="Worked on the payments-api service this session.",
                    answer_lines=[
                        "I implemented the checkout flow inside the payments-api service.",
                        "Card capture goes through the Stripe SDK, and every charge carries an idempotency key.",
                    ],
                    entities=["checkout", "payments-api", "stripe"],
                ),
                _Sess(
                    sid="s-checkout-retry",
                    tool="codex", team="eng", project="payments-api",
                    summary="More work on the payments-api service.",
                    answer_lines=[
                        "The payment-checkout retry path now backs off exponentially instead of hammering the provider.",
                        "That fixes the duplicate-charge incident customers hit last week.",
                    ],
                    entities=["checkout", "payments-api", "stripe"],
                ),
                # Decoy: same project + 'payments' vocabulary, different answer.
                _Sess(
                    sid="s-checkout-refund",
                    tool="codex", team="eng", project="payments-api",
                    summary="Refund handling in the payments-api service.",
                    answer_lines=[
                        "Added a refund endpoint to the payments-api that reverses a captured charge.",
                        "Partial refunds are supported down to the line-item level.",
                    ],
                    entities=["refund", "payments-api"],
                ),
                _Sess(
                    sid="s-checkout-blog",
                    tool="claude-code", team="marketing", project="website",
                    summary="Marketing content work on the website this session.",
                    answer_lines=[
                        "Wrote the launch announcement for the new one-click checkout experience.",
                        "The post leans on the faster purchase journey for returning shoppers.",
                    ],
                    entities=["checkout", "blog"],
                ),
            ],
            questions=[
                # vocabulary mismatch: 'card payment provider integration' vs 'Stripe SDK'.
                {"question": "Which microservice handles card capture and what third-party payment provider does it call?",
                 "gold": ["s-checkout-impl"], "type": "lookup"},
                {"question": "How did we stop customers being billed twice for one order?",
                 "gold": ["s-checkout-retry"], "type": "lookup"},
                # synthesis across eng + marketing.
                {"question": "Give the full story of the one-click checkout: which service builds it and how was it announced.",
                 "gold": ["s-checkout-impl", "s-checkout-blog"], "type": "synthesis"},
                # alias: 'payment-checkout' term; impl session calls it 'checkout'.
                {"question": "Where does the payment-checkout capture and retry logic live?",
                 "gold": ["s-checkout-impl", "s-checkout-retry"], "type": "alias"},
                # bridge: question points hard at the marketing/blog session, but the
                # gold is the *implementation* — reachable only via shared 'checkout'.
                {"question": "The feature we wrote the launch announcement for — which backend service actually implements it?",
                 "gold": ["s-checkout-impl"], "type": "bridge"},
            ],
        ),
        _Story(
            topic="auth",
            sessions=[
                _Sess(
                    sid="s-auth-jwt",
                    tool="claude-code", team="eng", project="session-service",
                    summary="Token work in the session-service.",
                    answer_lines=[
                        "We replaced opaque bearer tokens with signed JWTs in the session-service.",
                        "Access tokens live for fifteen minutes; refresh tokens for seven days.",
                    ],
                    entities=["authentication", "session-service", "jwt"],
                ),
                _Sess(
                    sid="s-auth-oauth",
                    tool="kilo-code", team="eng", project="session-service",
                    summary="Sign-in provider work in the session-service.",
                    answer_lines=[
                        "Added Google as a social sign-in option in the session-service.",
                        "External profiles are mapped onto internal user records on first login.",
                    ],
                    entities=["authentication", "session-service", "oauth"],
                ),
                _Sess(
                    sid="s-auth-ratelimit",
                    tool="codex", team="ops", project="gateway",
                    summary="Gateway hardening this session.",
                    answer_lines=[
                        "The gateway now throttles the sign-in route to ten tries per minute per IP.",
                        "The goal is to blunt password-guessing bots hitting the session-service.",
                    ],
                    entities=["authentication", "gateway", "session-service"],
                ),
                # Decoy: 'session' vocabulary but about user-session storage, not auth.
                _Sess(
                    sid="s-auth-decoy-redis",
                    tool="codex", team="eng", project="session-service",
                    summary="Storage work in the session-service.",
                    answer_lines=[
                        "Moved ephemeral user session state into Redis for faster reads.",
                        "This is unrelated to login; it is just where we cache live session blobs.",
                    ],
                    entities=["session-service", "redis"],
                ),
            ],
            questions=[
                {"question": "What did we change the access-token format to and how long are they valid?",
                 "gold": ["s-auth-jwt"], "type": "lookup"},
                {"question": "Can users log in with a social account, and which one?",
                 "gold": ["s-auth-oauth"], "type": "lookup"},
                # vocabulary mismatch: brute-force/password-guessing vs credential-stuffing.
                {"question": "How are we defending against bots that brute-force passwords on the login page?",
                 "gold": ["s-auth-ratelimit"], "type": "lookup"},
                {"question": "Summarize every authentication change across the session-service and the gateway.",
                 "gold": ["s-auth-jwt", "s-auth-oauth", "s-auth-ratelimit"], "type": "synthesis"},
                # bridge: question points at the gateway throttling session; the gold
                # is the token-format session, reachable only via shared session-service.
                {"question": "We throttled the sign-in route at the gateway — what token format does the service it shields hand out?",
                 "gold": ["s-auth-jwt"], "type": "bridge"},
            ],
        ),
        _Story(
            topic="ci",
            sessions=[
                _Sess(
                    sid="s-ci-flaky",
                    tool="claude-code", team="eng", project="ci-pipeline",
                    summary="Investigated test instability in the ci-pipeline.",
                    answer_lines=[
                        "The intermittent test failures traced back to a shared Postgres fixture left dirty between runs.",
                        "Giving each test its own isolated fixture made the ci-pipeline green and stable.",
                    ],
                    entities=["ci-pipeline", "postgres", "testing"],
                ),
                _Sess(
                    sid="s-ci-cache",
                    tool="codex", team="eng", project="ci-pipeline",
                    summary="Performance work on the ci-pipeline.",
                    answer_lines=[
                        "Caching the dependency install layer roughly halved ci-pipeline wall-clock time.",
                        "The cache key hashes the lockfile so it busts correctly on upgrades.",
                    ],
                    entities=["ci-pipeline", "caching"],
                ),
            ],
            questions=[
                # vocabulary mismatch: 'randomly failing' vs 'flaky', 'database' vs 'Postgres'.
                {"question": "Why were tests randomly failing and what database state caused it?",
                 "gold": ["s-ci-flaky"], "type": "lookup"},
                {"question": "What change cut our build times roughly in half?",
                 "gold": ["s-ci-cache"], "type": "lookup"},
            ],
        ),
        _Story(
            topic="search",
            sessions=[
                _Sess(
                    sid="s-search-embed",
                    tool="claude-code", team="eng", project="search-service",
                    summary="Recall work in the search-service.",
                    answer_lines=[
                        "The search-service now turns documents into MiniLM vectors stored in LanceDB.",
                        "Meaning-based matching improved a lot for paraphrased queries.",
                    ],
                    entities=["search-service", "lancedb", "embeddings"],
                ),
                _Sess(
                    sid="s-search-rerank",
                    tool="codex", team="eng", project="search-service",
                    summary="Precision work in the search-service.",
                    answer_lines=[
                        "Added a cross-encoder stage to the search-service that reorders the top fifty hits.",
                        "It noticeably sharpened the very top of the result list.",
                    ],
                    entities=["search-service", "reranking"],
                ),
            ],
            questions=[
                {"question": "How does the search-service match on meaning rather than keywords, and where do the vectors live?",
                 "gold": ["s-search-embed"], "type": "lookup"},
                {"question": "What did we add to sharpen the ordering of the best few search hits?",
                 "gold": ["s-search-rerank"], "type": "lookup"},
                {"question": "Walk through the search-service retrieval pipeline end to end.",
                 "gold": ["s-search-embed", "s-search-rerank"], "type": "synthesis"},
                # bridge: points at the reranking session; gold is the embeddings
                # session, reachable only via shared search-service.
                {"question": "We bolted a cross-encoder reorder step onto the results — how does the same service fetch its initial candidates?",
                 "gold": ["s-search-embed"], "type": "bridge"},
            ],
        ),
        _Story(
            topic="billing",
            sessions=[
                _Sess(
                    sid="s-billing-invoices",
                    tool="claude-code", team="eng", project="billing-service",
                    summary="Invoicing work in the billing-service.",
                    answer_lines=[
                        "Monthly bills are now assembled in the billing-service from aggregated metering data.",
                        "Each line on the bill maps to one metered usage category.",
                    ],
                    entities=["billing-service", "metering", "invoices"],
                ),
                _Sess(
                    sid="s-billing-dunning",
                    tool="codex", team="ops", project="billing-service",
                    summary="Failed-payment handling in the billing-service.",
                    answer_lines=[
                        "A dunning workflow re-attempts declined cards over a three-day schedule.",
                        "If the last retry still fails, the account is flagged for manual review.",
                    ],
                    entities=["billing-service", "dunning"],
                ),
            ],
            questions=[
                {"question": "How do we put together a customer's monthly bill and what data drives it?",
                 "gold": ["s-billing-invoices"], "type": "lookup"},
                {"question": "What happens when someone's card keeps getting declined?",
                 "gold": ["s-billing-dunning"], "type": "lookup"},
                # bridge: points at the dunning/failed-payment session; gold is the
                # invoice-generation session, reachable only via shared billing-service.
                {"question": "After the failed-payment retries give up, what process originally produced the bill they were chasing?",
                 "gold": ["s-billing-invoices"], "type": "bridge"},
            ],
        ),
        _Story(
            topic="onboarding",
            sessions=[
                _Sess(
                    sid="s-onboard-wizard",
                    tool="kilo-code", team="eng", project="web-app",
                    summary="New-user flow work in the web-app.",
                    answer_lines=[
                        "Built a step-by-step onboarding wizard in the web-app for first-time users.",
                        "It walks them through workspace setup and saves progress so they can resume.",
                    ],
                    entities=["onboarding", "web-app"],
                ),
            ],
            questions=[
                {"question": "How does a brand-new user get their workspace set up the first time they sign in?",
                 "gold": ["s-onboard-wizard"], "type": "lookup"},
            ],
        ),
    ]


# Standalone distractor sessions — unrelated work that makes retrieval realistic
# (a corpus where every session is on-topic is trivially searchable).
_DISTRACTORS = [
    ("s-distract-logging", "claude-code", "eng", "platform",
     "Standardize structured logging", "Rolled out structured JSON logging across platform services."),
    ("s-distract-docs", "claude-code", "eng", "docs",
     "Rewrite the API reference docs", "Rewrote the public API reference documentation for clarity."),
    ("s-distract-mobile", "kilo-code", "eng", "mobile-app",
     "Fix iOS layout bug", "Fixed a layout bug on the iOS mobile-app settings screen."),
    ("s-distract-analytics", "codex", "eng", "analytics",
     "Add funnel analytics events", "Instrumented funnel analytics events for the signup flow."),
    ("s-distract-infra", "codex", "ops", "infra",
     "Upgrade Kubernetes nodes", "Upgraded the Kubernetes node pool to the latest patch version."),
    ("s-distract-i18n", "claude-code", "eng", "web-app",
     "Add German translations", "Added German translations to the web-app using the i18n framework."),
]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _pad_body(answer_lines: list[str], target_chars: int, salt: int) -> list[str]:
    """Interleave the answer lines among deterministic filler to ~target_chars.

    The salt rotates the filler order per session so sessions don't read
    identically (which would make FTS/vectors degenerate). Fully deterministic.
    """
    lines: list[str] = []
    n_filler = len(_FILLER)
    fi = salt % n_filler
    ai = 0
    # Plant each answer line after a few filler lines, then keep padding.
    while sum(len(x) for x in lines) < target_chars:
        # Roughly every 3rd line, drop in the next un-planted answer line.
        if ai < len(answer_lines) and len(lines) % 3 == 1:
            lines.append(answer_lines[ai])
            ai += 1
        else:
            lines.append(_FILLER[fi % n_filler])
            fi += 1
        # Safety valve so a tiny target can't drop an answer line.
        if len(lines) > 200:
            break
    # Ensure all answer lines made it in (buried, but present).
    for rem in answer_lines[ai:]:
        lines.insert(len(lines) // 2, rem)
    return lines


def _to_session(s: _Sess, salt: int, body_chars: int) -> NormalizedSession:
    # Generic, non-leaking title when none is given (the answer must be earned
    # from the buried transcript, not read off the catalog title).
    title = s.title or f"Working session on {s.project}"
    body_lines = _pad_body(s.answer_lines, body_chars, salt)
    messages: list[Message] = []
    # Open generically so the user turn doesn't leak the answer either.
    messages.append(Message(id=f"{s.sid}-m0", role="user",
                            text=f"Let's keep working on the {s.project} project."))
    for i, line in enumerate(body_lines, start=1):
        role = "assistant" if i % 2 == 1 else "user"
        messages.append(Message(id=f"{s.sid}-m{i}", role=role, text=line))
    return NormalizedSession(
        id=s.sid,
        tool=s.tool,  # type: ignore[arg-type]
        title=title,
        project=s.project,
        message_count=len(messages),
        models=["synthetic"],
        preview=s.summary,
        messages=messages,
    )


@dataclass
class CorpusItem:
    session: NormalizedSession
    summary: str
    entities: list[str]
    team: str
    topic: str


@dataclass
class Corpus:
    items: list[CorpusItem]
    questions: list[dict[str, Any]]

    @property
    def sessions(self) -> list[NormalizedSession]:
        return [it.session for it in self.items]

    def summary_for(self, sid: str) -> str:
        for it in self.items:
            if it.session.id == sid:
                return it.summary
        return ""


def build_corpus(body_chars: int = 2400) -> Corpus:
    """Build the deterministic evaluation corpus + silver question set.

    Args:
        body_chars: approximate transcript length per session (buries the answer
            so plain keyword search on a long session is genuinely challenged).

    Returns:
        Corpus with ``items`` (session + summary + planted entities + team/topic)
        and ``questions`` (each ``{"question", "gold", "type", "topic"}``).
    """
    items: list[CorpusItem] = []
    questions: list[dict[str, Any]] = []

    salt = 0
    for story in _storylines():
        for s in story.sessions:
            items.append(CorpusItem(
                session=_to_session(s, salt=salt, body_chars=body_chars),
                summary=s.summary,
                entities=list(s.entities),
                team=s.team,
                topic=story.topic,
            ))
            salt += 1
        for q in story.questions:
            questions.append({**q, "topic": story.topic})

    # Distractors carry no questions; they only make retrieval non-trivial.
    for sid, tool, team, project, title, summary in _DISTRACTORS:
        sess = _Sess(sid=sid, tool=tool, team=team, project=project, title=title,
                     summary=summary, answer_lines=[summary], entities=[])
        items.append(CorpusItem(
            session=_to_session(sess, salt=salt, body_chars=body_chars),
            summary=summary, entities=[], team=team, topic="distractor",
        ))
        salt += 1

    return Corpus(items=items, questions=questions)
