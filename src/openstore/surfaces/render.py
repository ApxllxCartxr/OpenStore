# OpenStore surfaces — shared render helper + UI shell (S24).
#
# Templating stays 100% read_text() + .replace() (no Jinja — new dependency +
# full-surface refactor for no gain). render_template() injects the shared
# tokens every page gets; page-specific tokens arrive as pre-rendered strings
# (JSON via _safe_json at the call site, matching the existing convention).
#
# Stage-24 scope note (DECISION-044): the shell (app.css, js/*) ships here
# and every NEW page uses it. The seven legacy studio templates keep their
# inline markup byte-stable except a one-line CSRF-header addition each —
# churning the passkey-signing pages' JS wholesale would risk the money
# ceremony for cosmetic dedup; full migration is deferred, not abandoned.

from __future__ import annotations

import html as _html
import json
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse

TEMPLATES = Path(__file__).resolve().parent / "templates"

NAV_LINKS: tuple[tuple[str, str], ...] = (
    ("/merchant", "Dashboard"),
    ("/merchant/catalog", "Catalog"),
    ("/merchant/orders", "Orders"),
    ("/merchant/campaigns", "Campaigns"),
    ("/merchant/policies", "Policies"),
    ("/merchant/merchandising", "Merchandising"),
    ("/merchant/settings", "Settings"),
)


def render_nav(active: str = "") -> str:
    """Shared merchant-console nav. `active` is the current path prefix."""
    items = []
    for href, label in NAV_LINKS:
        cls = ' class="active" aria-current="page"' if active == href else ""
        items.append(f'<a href="{href}"{cls}>{_html.escape(label)}</a>')
    items.append('<a href="/merchant/login">Sign in</a>')
    return "<nav>" + "".join(items) + "</nav>"


def render_head(title: str) -> str:
    """Shared <head> fragment: charset, viewport, title, shell stylesheet."""
    return (
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_html.escape(title)} — OpenStore merchant console</title>\n"
        '<link rel="stylesheet" href="/static/app.css">'
    )


def csrf_meta(csrf_token: str) -> str:
    """<meta> carrying the per-session CSRF token for fetch() callers.
    Empty content when the viewer holds no session (CSRF is only enforced
    when a session cookie is present, so anonymous callers need nothing)."""
    return f'<meta name="csrf-token" content="{_html.escape(csrf_token)}">'


def inject_head(html_text: str, extra: str) -> str:
    """Insert `extra` markup before </head> without touching template bytes
    otherwise. Lets session-aware handlers add the CSRF meta (and the shell
    stylesheet link) to legacy templates that predate the shell."""
    if "</head>" not in html_text:
        return html_text
    return html_text.replace("</head>", extra + "\n</head>", 1)


def render_template(
    name: str,
    title: str,
    body: str,
    *,
    active: str = "",
    csrf_token: str = "",
    flash: str = "",
) -> HTMLResponse:
    """Render a merchant-console page around `body` HTML.

    `body` may carry page-specific placeholders already substituted by the
    caller; the shared __NAV__/__HEAD__/__CSRF_TOKEN__/__FLASH__ tokens are
    substituted here so pages never forget them."""
    page = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n__HEAD__\n__CSRF_TOKEN__\n</head>\n'
        "<body>\n__NAV__\n<main>\n__FLASH__\n"
        + body
        + "\n</main>\n</body>\n</html>"
    )
    _ = name  # pages are inline bodies in S24; file-backed names reserved.
    page = page.replace("__HEAD__", render_head(title))
    page = page.replace("__CSRF_TOKEN__", csrf_meta(csrf_token))
    page = page.replace("__NAV__", render_nav(active))
    page = page.replace(
        "__FLASH__",
        f'<p class="flash">{_html.escape(flash)}</p>' if flash else "",
    )
    return HTMLResponse(page)


def safe_json(value: Any) -> str:
    """JSON for direct embedding in <script> (matches studio._safe_json)."""
    return json.dumps(value, separators=(",", ":"))

