"""The `/agentic` console, rendered server-side as an instrument panel.

Mono-dominant, hairline rules, `--bg-sunken` panels, tabular figures, **no
animation** (§4). Data density over whitespace, because SPEC §14 says this is
what somebody checks at 2am — and at 2am you want the numbers, not the easing
curves.

No component references a literal colour. Every value is a token from
`design/tokens.css`, so the console inherits the palette and the dark theme
without knowing either.

Server-rendered on purpose: the console is the Merchant's own surface, it has no
offline story to tell, and a client-side app here would be a second place where
policy could disagree with the Gate.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any

from openstore.sidecar.evidence.bundle import format_rupees

#: The console's own styling. The tokens come from `design/tokens.css`, which is
#: served alongside; these are the density rules that make it an instrument
#: panel rather than the shop.
CONSOLE_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper, #f6f5f2);
  color: var(--ink, #16161a);
  font-family: 'Iosevka Term SS08', ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}
header { border-bottom: 1px solid var(--line, #e2e0da); padding: 12px 16px; }
h1 { font-size: 13px; font-weight: 600; margin: 0; letter-spacing: 0.02em; }
nav { display: flex; flex-wrap: wrap; gap: 0; border-bottom: 1px solid var(--line, #e2e0da); }
nav a {
  padding: 10px 14px; min-height: 44px; display: flex; align-items: center;
  color: var(--ink, #16161a); text-decoration: none;
  border-right: 1px solid var(--line, #e2e0da);
}
nav a[aria-current='page'] { background: var(--bg-sunken, #eceae4); font-weight: 600; }
nav a:focus-visible, a:focus-visible, button:focus-visible { outline: 2px solid var(--accent-2, #2f6b95); outline-offset: 2px; }
main { padding: 16px; }
section { margin-bottom: 24px; }
h2 { font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 8px;
     color: var(--comment, #9a9aa1); font-weight: 600; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line, #e2e0da); }
th { font-weight: 600; color: var(--comment, #9a9aa1); font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.06em; }
td.num { text-align: right; }
.panel { background: var(--bg-sunken, #eceae4); border: 1px solid var(--line, #e2e0da); padding: 12px; }
.banner { border: 1px solid var(--accent, #c2415a); padding: 10px 12px; margin-bottom: 16px; }
.banner strong { color: var(--accent, #c2415a); }
.ok { color: var(--ok, #2f8a4d); }
.warn { color: var(--accent, #c2415a); }
.muted { color: var(--comment, #9a9aa1); }
.note { max-width: 72ch; }
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
"""

TABS = (
    ("keys", "Keys"),
    ("policy", "Policy"),
    ("provider", "Provider"),
    ("authority", "Authority"),
    ("exposure", "Exposure"),
    ("agents", "Agents"),
    ("receipts", "Receipts"),
    ("health", "Health"),
)


def _e(value: Any) -> str:
    return html.escape(str(value))


@dataclass
class ConsoleState:
    """Everything the console renders, assembled by the caller.

    A value object rather than a live handle: the console reads state, it does
    not reach into the Gate. Policy edits go the other way, through an explicit
    write.
    """

    merchant_domain: str
    keys: list[dict[str, Any]]
    policy: dict[str, Any]
    provider: dict[str, Any]
    authority: dict[str, Any]
    exposure: dict[str, Any]
    agents: list[dict[str, Any]]
    receipts: list[dict[str, Any]]
    overdue_holds: list[dict[str, Any]]
    dev_profile_hosts: tuple[str, ...] = ()
    export_acknowledged: bool = True


def page(state: ConsoleState, tab: str) -> str:
    body = _TABS[tab](state)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(state.merchant_domain)} — OpenStore console</title>
<link rel="stylesheet" href="/agentic/static/tokens.css">
<style>{CONSOLE_CSS}</style>
</head>
<body>
<header><h1>{_e(state.merchant_domain)} · /agentic</h1></header>
<nav>{"".join(_tab_link(name, label, tab) for name, label in TABS)}</nav>
<main>
{_banners(state)}
{body}
</main>
</body>
</html>"""


def _tab_link(name: str, label: str, current: str) -> str:
    marker = ' aria-current="page"' if name == current else ""
    return f'<a href="/agentic/{name}"{marker}>{_e(label)}</a>'


def _banners(state: ConsoleState) -> str:
    """The two things that cannot be dropped (cut #2).

    Overdue holds, because the sidecar owns all three expiry clocks and a wedged
    sidecar holds stock forever — the only defence is that somebody can see it.
    And the dev-allowlist banner, because an exception nobody can see is one that
    outlives its reason.
    """
    out = []
    if state.dev_profile_hosts:
        hosts = ", ".join(_e(h) for h in state.dev_profile_hosts)
        out.append(
            f'<div class="banner"><strong>Dev profile allowlist active</strong> — {hosts}. '
            "This is the SSRF exception for the compose demo. Development only; the sidecar "
            "refuses to boot with this set alongside live provider keys.</div>"
        )
    if not state.export_acknowledged:
        out.append(
            '<div class="banner"><strong>Save your key export</strong> — first run does not '
            "continue until the encrypted export is acknowledged as saved. It is the only way "
            "back if this deployment is lost.</div>"
        )
    if state.overdue_holds:
        out.append(
            f'<div class="banner"><strong>{len(state.overdue_holds)} hold(s) past their '
            "deadline</strong> — stock is held and the sweeper has not released it. Release "
            "goes through the sidecar, never a Merchant-side write.</div>"
        )
    return "\n".join(out)


def _keys(state: ConsoleState) -> str:
    rows = "".join(
        f"<tr><td>{_e(k['kid'])}</td><td>{_e(k['created_at'])}</td>"
        f"<td>{'<span class=\"warn\">revoked ' + _e(k['revoked_at']) + '</span>' if k.get('revoked_at') else '<span class=\"ok\">active</span>'}</td></tr>"
        for k in state.keys
    )
    return f"""<section>
<h2>Signing keys</h2>
<table><thead><tr><th>kid</th><th>created</th><th>state</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note muted">Rotation is additive by <code>kid</code> and never re-signs history.
Revocation invalidates the future, not the past: a receipt signed before revocation stays
valid forever, which is why rotating after an incident does not destroy your own evidence.</p>
</section>"""


def _policy(state: ConsoleState) -> str:
    p = state.policy
    return f"""<section>
<h2>Policy</h2>
<table><tbody>
<tr><th>Per-order cap</th><td class="num">{_e(format_rupees(p['per_order_cap_minor']))}</td></tr>
<tr><th>Per-order line count</th><td class="num">{_e(p['per_order_line_count'])}</td></tr>
<tr><th>Per-group quantity</th><td class="num">{_e(p['per_group_qty'])}</td></tr>
<tr><th>Blocked tags</th><td>{_e(", ".join(p['blocked_tags']) or "—")}</td></tr>
<tr><th>Agent window</th><td>{'open' if p['window_open'] else '<span class="warn">closed</span>'}</td></tr>
</tbody></table>
<p class="note muted">Count, quantity and caps are evaluated <strong>at the Product Group</strong>,
so buying two of each colour cannot walk through a two-per-order cap. Stated here so nobody
wonders why a basket of three different totes counted as one item.</p>
</section>"""


def _provider(state: ConsoleState) -> str:
    p = state.provider
    return f"""<section>
<h2>Provider</h2>
<table><tbody>
<tr><th>Adapter</th><td>{_e(p['adapter'])}</td></tr>
<tr><th>Declared methods</th><td>{_e(", ".join(p['declared_methods']))}</td></tr>
<tr><th>Enabled methods</th><td>{_e(", ".join(p['enabled_methods']))}</td></tr>
<tr><th>Link lifetime</th><td class="num">{_e(p['link_lifetime_minutes'])} min</td></tr>
<tr><th>Webhooks</th><td>{_e(p['webhook_status'])}</td></tr>
</tbody></table>
<p class="note muted">Anything outside the enabled set refuses <code>method-not-supported</code>
naming what <em>is</em> enabled. The adapter declares what it can do; you decide what it may.</p>
</section>"""


def _authority(state: ConsoleState) -> str:
    a = state.authority
    return f"""<section>
<h2>Authority kinds accepted</h2>
<table><tbody>
<tr><th>Kinds</th><td>{_e(", ".join(a['kinds']))}</td></tr>
<tr><th>confirmed-intent mechanisms</th><td>{_e(", ".join(a['mechanisms']))}</td></tr>
<tr><th>Refused</th><td><code>mandate</code> — defined, registered, refused in v1</td></tr>
</tbody></table>
<p class="note muted">An OTP to a Contact Point is not a mechanism and cannot be configured as
one: it proves control of a phone number, which is what RTO fraud already defeats, and it binds
no funding instrument.</p>
</section>"""


def _exposure(state: ConsoleState) -> str:
    rows = "".join(
        f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in sorted(state.exposure.items())
    )
    return f"""<section>
<h2>Exposure <span class="muted">(read-only)</span></h2>
<table><thead><tr><th>Policy</th><th>Visible to agents</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note muted">Which policies are public to Buyer Agents. Read-only in this build.</p>
</section>"""


def _agents(state: ConsoleState) -> str:
    rows = "".join(
        f"<tr><td>{_e(a['agent_id'])}</td><td>{_e(a['tier'])}</td><td>{_e(a.get('last_seen', '—'))}</td>"
        f"<td>{'<span class=\"warn\">blocked</span>' if a.get('blocked') else '<span class=\"ok\">allowed</span>'}</td></tr>"
        for a in state.agents
    )
    return f"""<section>
<h2>Agents</h2>
<table><thead><tr><th>agent_id</th><th>tier</th><th>last seen</th><th>state</th></tr></thead>
<tbody>{rows or '<tr><td colspan="4" class="muted">No agents have registered yet.</td></tr>'}</tbody></table>
<p class="note muted">Reputation buys throughput only. An allowlisted agent gets a higher rate
limit and not one capability more — <code>confirm</code> without a fresh Authority is refused
either way.</p>
</section>"""


def _receipts(state: ConsoleState) -> str:
    rows = "".join(
        f"<tr><td><a href=\"/receipt/{_e(r['receipt_id'])}\">{_e(r['receipt_id'])}</a></td>"
        f"<td class=\"num\">{_e(format_rupees(r['total_minor']))}</td>"
        f"<td>{_e(r['authority'])}</td><td>v{_e(r['version'])}</td></tr>"
        for r in state.receipts
    )
    return f"""<section>
<h2>Receipts</h2>
<table><thead><tr><th>receipt_id</th><th>total</th><th>authority</th><th>version</th></tr></thead>
<tbody>{rows or '<tr><td colspan="4" class="muted">No sealed receipts yet.</td></tr>'}</tbody></table>
<p class="note muted">Receipts open by their unguessable id with no login, which is why the
viewer sits outside this console's auth boundary.</p>
</section>"""


def _health(state: ConsoleState) -> str:
    rows = "".join(
        f"<tr><td>{_e(h['order_id'])}</td><td>{_e(h['status'])}</td>"
        f"<td>{_e(h['deadline'])}</td><td class=\"num\">{_e(format_rupees(h['amount_minor']))}</td></tr>"
        for h in state.overdue_holds
    )
    return f"""<section>
<h2>Overdue holds</h2>
<table><thead><tr><th>order</th><th>status</th><th>deadline</th><th>held</th></tr></thead>
<tbody>{rows or '<tr><td colspan="4" class="ok">Nothing overdue.</td></tr>'}</tbody></table>
<p class="note muted">The sidecar owns all three expiry clocks and the Merchant never
self-expires, so a wedged or stopped sidecar holds stock indefinitely and the only defence is
that somebody can see it. The documented release path goes through the sidecar, never a
Merchant-side write.</p>
</section>
<section>
<h2>Counters</h2>
<p class="note muted">Gate refusals by reason code, authority outcomes by kind, provider webhook
lag and reconciler drift emit to structured logs and <code>/healthz</code>. They have no panel in
this build.</p>
</section>"""


_TABS = {
    "keys": _keys,
    "policy": _policy,
    "provider": _provider,
    "authority": _authority,
    "exposure": _exposure,
    "agents": _agents,
    "receipts": _receipts,
    "health": _health,
}
