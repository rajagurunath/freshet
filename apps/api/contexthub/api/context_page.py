"""PR context link — token-gated HTML context page.

Provides:
  sign_share_token(session_id, secret, ttl_seconds) → (token, expiry)
  verify_share_token(session_id, token, expiry, secret) → bool
  render_context_page(...)                            → HTML string

The share endpoint (POST /v1/sessions/{id}/share) mints a short-lived HMAC
token; GET /c/{session_id}?t=<token>&expiry=<n> renders the page without
requiring a cookie or Bearer auth — useful for sharing with reviewers who
do not have a hub account.
"""

from __future__ import annotations

import hashlib
import hmac
import html as _html_module
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Token utilities
# ---------------------------------------------------------------------------

def sign_share_token(session_id: str, secret: str, ttl_seconds: int = 86400) -> tuple[str, int]:
    """Mint an HMAC-SHA256 share token for *session_id*.

    Returns (token_hex, expiry_unix).  Default TTL is 24 hours.
    """
    expiry = int(time.time()) + ttl_seconds
    msg = f"{session_id}:{expiry}".encode()
    token = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return token, expiry


def verify_share_token(session_id: str, token: str, expiry: int, secret: str) -> bool:
    """Return True iff *token* is a valid, unexpired HMAC for *session_id*.

    Timing-safe comparison; explicit expiry check before comparing.
    """
    if int(time.time()) > expiry:
        return False
    msg = f"{session_id}:{expiry}".encode()
    expected = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

_CSS = """
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:            #FBFAF8;
    --bg-elevated:   #FFFFFF;
    --bg-sunken:     #F4F2EE;
    --ink:           #1A1815;
    --ink-soft:      #56524B;
    --ink-faint:     #8E897F;
    --border:        #E7E3DB;
    --border-strong: #D8D2C7;
    --accent:        #F2541B;
    --accent-ink:    #C8400F;
    --accent-wash:   #FCEDE6;
    font-family: system-ui, -apple-system, 'Segoe UI', Inter, sans-serif;
  }

  body {
    background: var(--bg);
    color: var(--ink);
    font-size: 15px;
    line-height: 1.6;
    padding: 32px 16px 64px;
  }

  .container {
    max-width: 760px;
    margin: 0 auto;
  }

  header {
    border-bottom: 1px solid var(--border);
    padding-bottom: 24px;
    margin-bottom: 32px;
  }

  .hub-label {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 12px;
  }

  h1 {
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--ink);
    margin-bottom: 8px;
  }

  .meta {
    font-size: 13px;
    color: var(--ink-faint);
  }

  .meta span + span::before { content: " · "; }

  .section { margin-bottom: 32px; }

  .section-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 12px;
    border-bottom: 1px solid var(--border);
    padding-bottom: 6px;
  }

  .summary-box {
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    color: var(--ink-soft);
    font-size: 14px;
    line-height: 1.7;
  }

  .links-list { list-style: none; }
  .links-list li { margin-bottom: 8px; }
  .links-list a {
    color: var(--accent-ink);
    text-decoration: none;
    font-size: 14px;
  }
  .links-list a:hover { text-decoration: underline; }
  .link-kind {
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    background: var(--accent-wash);
    color: var(--accent-ink);
    border-radius: 4px;
    padding: 1px 6px;
    margin-right: 6px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .message { margin-bottom: 16px; }

  .message-user .bubble {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 14px;
    color: var(--ink-soft);
  }

  .message-assistant .bubble {
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 14px;
    color: var(--ink);
  }

  .role-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-faint);
    margin-bottom: 4px;
  }

  details.tool-msg {
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 16px;
    background: var(--bg-elevated);
  }

  details.tool-msg summary {
    cursor: pointer;
    padding: 8px 12px;
    font-size: 12px;
    color: var(--ink-faint);
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    user-select: none;
  }

  details.tool-msg .tool-body {
    padding: 8px 12px 12px;
    font-size: 12px;
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    color: var(--ink-soft);
    border-top: 1px solid var(--border);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .graph-neighbor {
    display: inline-block;
    margin: 4px;
    padding: 4px 10px;
    border: 1px solid var(--border);
    border-radius: 20px;
    font-size: 12px;
    color: var(--ink-soft);
    background: var(--bg-elevated);
  }

  footer {
    margin-top: 48px;
    padding-top: 24px;
    border-top: 1px solid var(--border);
    font-size: 12px;
    color: var(--ink-faint);
    text-align: center;
  }
</style>
"""


def _e(s: str) -> str:
    """HTML-escape a string."""
    return _html_module.escape(str(s), quote=True)


def render_context_page(
    session_id: str,
    title: str,
    author: str,
    summary: Optional[str],
    messages: list[dict],
    links: list[dict],
    graph_neighbors: list[dict],
    pr_url: Optional[str],
    created_at: Optional[str] = None,
) -> str:
    """Render a token-gated context page as an HTML string.

    Design:
      • Header: hub label, session title, author, date.
      • "Why this PR" section: session summary.
      • Links section: PR / issue / doc links from session.links[].
      • Decision timeline: assistant prose messages shown inline;
        tool messages collapsed inside <details> elements.
      • Graph neighbors (optional): chip list of related graph nodes.
    """
    parts: list[str] = []

    def w(s: str) -> None:
        parts.append(s)

    w("<!DOCTYPE html>")
    w("<html lang='en'>")
    w("<head>")
    w(f"<meta charset='utf-8'>")
    w(f"<meta name='viewport' content='width=device-width, initial-scale=1'>")
    w(f"<title>{_e(title)} — Context Hub</title>")
    w(_CSS)
    w("</head>")
    w("<body>")
    w("<div class='container'>")

    # --- Header ---
    w("<header>")
    w("<div class='hub-label'>Context Hub · Agent context</div>")
    w(f"<h1>{_e(title)}</h1>")
    w("<div class='meta'>")
    if author:
        w(f"<span>{_e(author)}</span>")
    if created_at:
        w(f"<span>{_e(created_at[:10])}</span>")
    w(f"<span>Session&nbsp;{_e(session_id[:8])}&hellip;</span>")
    w("</div>")
    w("</header>")

    # --- Why this PR / summary ---
    w("<div class='section'>")
    w("<div class='section-title'>Why this PR</div>")
    if summary:
        w(f"<div class='summary-box'>{_e(summary)}</div>")
    else:
        w("<div class='summary-box' style='color:var(--ink-faint)'>No summary available.</div>")
    w("</div>")

    # --- Links ---
    all_links = list(links)
    if pr_url:
        all_links.insert(0, {"kind": "pr", "url": pr_url, "label": pr_url})

    if all_links:
        w("<div class='section'>")
        w("<div class='section-title'>Links</div>")
        w("<ul class='links-list'>")
        for lnk in all_links:
            kind = lnk.get("kind", "link")
            url = lnk.get("url", "")
            label = lnk.get("label") or url
            w(f"<li><span class='link-kind'>{_e(kind)}</span>"
              f"<a href='{_e(url)}' target='_blank' rel='noopener noreferrer'>{_e(label)}</a></li>")
        w("</ul>")
        w("</div>")

    # --- Decision timeline ---
    visible = [m for m in messages if m.get("role") in ("user", "assistant", "tool")]
    if visible:
        w("<div class='section'>")
        w("<div class='section-title'>Decision timeline</div>")
        for msg in visible:
            role = msg.get("role", "")
            text = msg.get("text", "")

            if role == "tool":
                tool_name = msg.get("tool_name", "Tool")
                w(f"<details class='tool-msg'>")
                w(f"<summary>▸ {_e(tool_name)}: {_e(text[:80])}{'…' if len(text) > 80 else ''}</summary>")
                w(f"<div class='tool-body'>{_e(text)}</div>")
                w("</details>")
            else:
                css_cls = "message-user" if role == "user" else "message-assistant"
                role_label = "You" if role == "user" else "Assistant"
                w(f"<div class='message {_e(css_cls)}'>")
                w(f"<div class='role-label'>{role_label}</div>")
                w(f"<div class='bubble'>{_e(text)}</div>")
                w("</div>")
        w("</div>")

    # --- Graph neighbors ---
    if graph_neighbors:
        w("<div class='section'>")
        w("<div class='section-title'>Related concepts</div>")
        for node in graph_neighbors:
            name = node.get("name", "")
            kind = node.get("kind", "")
            w(f"<span class='graph-neighbor'>{_e(kind)}: {_e(name)}</span>")
        w("</div>")

    w("<footer>Shared via <strong>Context Hub</strong> — agent context page</footer>")
    w("</div>")
    w("</body>")
    w("</html>")

    return "\n".join(parts)
