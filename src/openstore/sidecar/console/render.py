"""The `/agentic` console, rendered server-side as an instrument panel.

Tabular figures, hairline rules, `--bg-sunken` panels, **no
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
import json
from dataclasses import dataclass, field
from typing import Any

from openstore.sidecar.evidence.bundle import format_rupees

#: The console's own styling. The tokens come from `design/tokens.css`, which is
#: served alongside; these are the density rules that make it an instrument
#: panel rather than the shop.
CONSOLE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }

/* The app ground is the SUNKEN token and cards are the RAISED one, which is
   what makes this read as a console rather than a document: grey paper under
   white cards. One rule, no second palette.

   Nothing here carries a literal-colour fallback. It used to, and because
   `--paper` and `--ink` are not token names the fallbacks were what ALWAYS
   rendered — a surface drawing its own colours beside the ones it imported. */
body {
  margin: 0;
  background: var(--bg-sunken);
  color: var(--fg);
  font-family: var(--font-body);
  font-size: 15px;
  line-height: 1.5;
  /* Figures line up column-wise everywhere in here. This is a screen people
     read down, not across. */
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}

/* Two faces, each with one job. EB Garamond is the wordmark and the page
   title — the only two places a serif appears, so it reads as identity rather
   than as decoration. Open Sauce Sans carries every number, label and control,
   because a dashboard is read at a glance and a serif at 13px in a table is a
   serif nobody can scan.

   Every family here is named as a token ROLE and never as a face: the token
   layer decides which font a role resolves to, so a swap there cannot leave
   this console drawing its own typography beside the one it imported. */
.brand b, h1 {
  font-family: var(--font-display);
  font-weight: 400;
  letter-spacing: 0;
}

/* The figure-bearing surfaces take the mono ROLE. It resolves to the same sans
   in this token layer — the columns are held in line by `tabular-nums` rather
   than by a monospaced face — but naming the role is what keeps a future
   monospaced token landing on the tables and not on the prose. */
table, .tile .v, .chip, code {
  font-family: var(--font-mono);
}

/* Sidebar and content. The nav is a column because the console has eleven
   destinations and a horizontal strip of eleven wraps to two rows on a laptop,
   which is where an operator actually reads this. */
.shell { display: grid; grid-template-columns: 1fr; min-height: 100vh; }
.side {
  background: var(--bg);
  border-right: 1px solid var(--line);
  padding: 14px 0 20px;
}
.brand { padding: 4px 16px 16px; }
.brand b { display: block; font-size: 22px; line-height: 1.1; }
.brand span {
  color: var(--muted); font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.14em;
}

nav { display: flex; flex-wrap: wrap; gap: 2px; padding: 0 8px; }
/* The group label is the whole reason this is navigable at a glance: it turns
   eleven equal links into four short lists with a subject each. */
.nav-group { display: contents; }
.nav-head {
  flex: 1 0 100%;
  padding: 12px 8px 4px;
  font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--comment);
}
nav a {
  display: flex; align-items: center; gap: 8px;
  padding: 0 10px; min-height: 32px; flex: 1 1 auto;
  color: var(--muted); text-decoration: none;
  border-radius: var(--radius);
  font-size: 14px;
}
nav a:hover { background: var(--bg-sunken); color: var(--fg); }
/* The current tab is marked by a rule at its edge as well as by weight and
   ground: three signals, none of them colour alone. */
nav a[aria-current='page'] {
  background: var(--bg-sunken); color: var(--fg); font-weight: 600;
  box-shadow: inset 2px 0 0 var(--accent-2);
}
nav a:focus-visible, a:focus-visible, button:focus-visible {
  outline: 2px solid var(--accent-2); outline-offset: 2px;
}

/* The top bar: who this deployment is, and whether it can do its job. Sticky,
   because the answer to "which shop am I looking at" must not scroll away on a
   screen where every shop's console looks alike. */
.topbar {
  position: sticky; top: 0; z-index: 2;
  display: flex; flex-wrap: wrap; gap: 10px 16px;
  align-items: baseline; justify-content: space-between;
  margin: -16px -16px 18px;
  padding: 12px 16px;
  background: var(--bg-raised);
  border-bottom: 1px solid var(--line);
}
.topbar .who b { font-size: 15px; font-weight: 600; }
.eyebrow {
  display: block; color: var(--muted);
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em;
}
.status { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  border: 1px solid var(--line); border-radius: 999px;
  padding: 2px 10px; font-size: 13px; color: var(--muted);
  background: var(--bg);
  white-space: nowrap;
}
.chip-warn { border-color: var(--accent); color: var(--accent); }

main { padding: 16px; min-width: 0; }
.page-head { margin-bottom: 18px; }
h1 { font-size: 30px; margin: 0; line-height: 1.15; }
.page-head p { margin: 4px 0 0; color: var(--muted); font-size: 14px; max-width: 68ch; }

/* A card is the unit of the dashboard. Sections keep their own heading so the
   markup still reads as a document with stylesheets off. */
section, .card {
  background: var(--bg-raised);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 14px;
  margin-bottom: 12px;
}
h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 10px;
     color: var(--muted); font-weight: 600; }

table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }
tr:last-child td { border-bottom: 0; }
th { font-weight: 600; color: var(--muted); font-size: 13px;
     text-transform: uppercase; letter-spacing: 0.06em; }
td.num, th.num { text-align: right; }

/* Stat tiles. The value is the one place the console drops tabular figures: a
   standalone number set in them looks loose, because every glyph is padded to
   the width of a zero. */
.tiles { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
         margin-bottom: 12px; }
.tile { background: var(--bg-raised); border: 1px solid var(--line);
        border-radius: var(--radius-lg); padding: 14px; }
.tile .k { color: var(--muted); font-size: 14px; }
.tile .v { font-variant-numeric: proportional-nums; font-size: 32px; font-weight: 600;
           line-height: 1.15; margin-top: 4px; letter-spacing: -0.02em; }
.tile .s { color: var(--muted); font-size: 14px; margin-top: 2px; }

/* Charts. One hue for every mark: these are single-series plots, so a second
   colour would encode nothing, and colouring bars by height double-encodes the
   height. Axis and grid are hairlines one step off the surface. */
.chart { width: 100%; height: auto; display: block; overflow: visible; }
.chart .grid-line { stroke: var(--line); stroke-width: 1; }
.chart .mark { fill: var(--accent-2); }
.chart .mark:hover { fill: var(--fg); }
.chart .tick { fill: var(--muted); font-size: 13px; }
.chart .val { fill: var(--fg); font-size: 13px; font-weight: 600; }
.chart-empty { color: var(--muted); padding: 18px 0; text-align: center; }
.two-up { display: grid; gap: 12px; grid-template-columns: 1fr; }

/* Raw documents. The console shows the bytes an agent is served, folded away
   by default: the rendered table is what an operator reads, and the JSON is
   what they paste into a bug report. */
details { margin-top: 10px; }
summary { cursor: pointer; color: var(--accent-2); font-size: 14px; }
summary:focus-visible { outline: 2px solid var(--accent-2); outline-offset: 2px; }
pre {
  background: var(--bg-sunken); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 10px 12px; overflow-x: auto; margin: 8px 0 0; font-size: 13px;
  max-height: 24rem; overflow-y: auto;
}

.banner { border: 1px solid var(--accent); border-radius: var(--radius-lg);
          background: var(--bg-raised); padding: 10px 12px; margin-bottom: 12px; }
.banner strong { color: var(--accent); }
.ok { color: var(--ok); }
.warn { color: var(--accent); }
.muted { color: var(--muted); }
.note { max-width: 72ch; }
p.note { margin: 10px 0 0; }

@media (min-width: 900px) {
  .shell { grid-template-columns: 208px 1fr; }
  .side { position: sticky; top: 0; height: 100vh; overflow-y: auto; }
  nav { flex-direction: column; }
  main { padding: 20px 28px 40px; }
  .topbar { margin: -20px -28px 22px; padding: 14px 28px; }
  .two-up { grid-template-columns: 1fr 1fr; }
}
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
"""

#: The nav, grouped. Eleven destinations in one flat column is a list you read
#: top to bottom every time because nothing tells you where to look; four short
#: groups is a place you learn. The grouping is by *who the screen is about* —
#: the trade, the strangers calling in, the limits the Merchant set, and the
#: machinery underneath — not by how often a tab is opened, which is a ranking
#: that goes stale and that nobody can verify.
TAB_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Trade",
        (("overview", "Overview"), ("receipts", "Receipts"), ("refunds", "Refunds")),
    ),
    (
        "Agents",
        (("agents", "Registered"), ("discovery", "Discovery"), ("exposure", "Exposure")),
    ),
    (
        "Limits",
        (("policy", "Policy"), ("authority", "Authority"), ("provider", "Provider")),
    ),
    ("Operations", (("keys", "Keys"), ("health", "Health"))),
)

#: The flat view, which is what routing and the tab dispatch read. Derived, so a
#: tab added to a group above is routable without a second edit — and a tab that
#: exists in one and not the other cannot happen.
TABS = tuple(tab for _group, tabs in TAB_GROUPS for tab in tabs)


#: One line under each page title. The console has nine destinations and the
#: names alone ("Authority", "Exposure") do not say what you are looking at.
_SUBTITLES = {
    "overview": "Agent-originated trade, as sealed by this sidecar",
    "keys": "What signs receipts, and what no longer does",
    "policy": "The limits the Gate evaluates on every basket",
    "provider": "What the payment adapter can do, and what it may do",
    "authority": "Which kinds of human approval this shop accepts",
    "exposure": "Which policies Buyer Agents are allowed to see",
    "agents": "Who has registered, and what their tier buys them",
    "discovery": "The documents an agent reads before it ever calls a tool",
    "receipts": "Sealed, hash-chained, verifiable without this server",
    "refunds": "What agents asked for, and what the shop did",
    "health": "Clocks, holds, and where the counters live",
}


# ── Charts ───────────────────────────────────────────────────────────────────
#
# Inline SVG, no library, no script. Every mark is ONE hue (`--accent-2`)
# because each plot here is a single series: a second colour would encode
# nothing, and shading bars by their own height double-encodes the height.
# `--accent` and `--ok` are deliberately not used — they mean warning and
# healthy everywhere else in this console, and a status colour spent on data
# stops meaning status.
#
# Each chart is drawn directly above the table of the same numbers, so no value
# is reachable only by hovering.

#: Bars are capped rather than filling their band, and the leftover is air.
_BAR_MAX = 24
#: The rounded data-end. Square at the baseline, round at the tip.
_BAR_R = 4


def _nice_ceiling(value: int) -> int:
    """Round an axis top up to something a person would have chosen."""
    if value <= 0:
        return 1
    # Annotated: `int ** int` is typed as Any, because a negative exponent
    # would return a float. The exponent here is a digit count and cannot be.
    step: int = 10 ** (len(str(value)) - 1)
    for multiple in (1, 2, 5, 10):
        if value <= step * multiple:
            return step * multiple
    return step * 10


def _column_path(x: int, y: int, w: int, h: int) -> str:
    """A column with a rounded cap and square feet, as one path.

    Drawn as a path rather than a `<rect rx>` because `rx` rounds all four
    corners: a bar that curves where it meets its own baseline looks like it is
    floating off the axis.
    """
    r = min(_BAR_R, w // 2, h)
    return (
        f"M{x},{y + h} V{y + r} Q{x},{y} {x + r},{y} "
        f"H{x + w - r} Q{x + w},{y} {x + w},{y + r} "
        f"V{y + h} Z"
    )


def _bar_path(x: int, y: int, w: int, h: int) -> str:
    """A horizontal bar: square at the axis, rounded at the tip."""
    r = min(_BAR_R, h // 2, w)
    return (
        f"M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} "
        f"V{y + h - r} Q{x + w},{y + h} {x + w - r},{y + h} "
        f"H{x} Z"
    )


def _columns(series: list[tuple[str, int]], fmt: Any, caption: str) -> str:
    """Value over time, as columns on a single baseline.

    Every coordinate is an integer. SVG has no need of fractional pixels here,
    and the money lint is right to refuse floats in this package rather than
    take a promise that a given float never touches paise.
    """
    if not series or all(v == 0 for _, v in series):
        return f'<p class="chart-empty">{_e(caption)}</p>'

    width, height = 640, 190
    pad_l, pad_r, pad_b, pad_t = 8, 8, 22, 16
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    top = _nice_ceiling(max(v for _, v in series))
    n = len(series)
    band = plot_w // n
    # The 2px separator is surface, not a stroke: neighbours read as distinct
    # because of the gap, never because a border was drawn around them.
    bar_w = min(_BAR_MAX, band - 2)
    peak = max(range(n), key=lambda i: series[i][1])

    marks: list[str] = []
    for i, (label, value) in enumerate(series):
        h = value * plot_h // top if top else 0
        x = pad_l + plot_w * i // n + (band - bar_w) // 2
        y = pad_t + plot_h - h
        mid = x + bar_w // 2
        if value > 0:
            marks.append(
                f'<path class="mark" d="{_column_path(x, y, bar_w, h)}">'
                f"<title>{_e(label)}: {_e(fmt(value))}</title></path>"
            )
        # Only the peak is labelled. A number over every column is chaos and
        # goes unread; the rest are in the table directly below.
        if i == peak and value > 0:
            marks.append(
                f'<text class="val" x="{mid}" y="{y - 5}" text-anchor="middle">{_e(fmt(value))}</text>'
            )
        if i % 2 == 0:
            marks.append(
                f'<text class="tick" x="{mid}" y="{height - 6}" text-anchor="middle">{_e(label)}</text>'
            )

    base = pad_t + plot_h
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{_e(caption)}">'
        f'<line class="grid-line" x1="{pad_l}" y1="{pad_t}" x2="{width - pad_r}" y2="{pad_t}"/>'
        f'<line class="grid-line" x1="{pad_l}" y1="{base}" x2="{width - pad_r}" y2="{base}"/>'
        f"{''.join(marks)}</svg>"
    )


def _bars(rows: list[tuple[str, int]], caption: str) -> str:
    """Counts by category, as horizontal bars with the value at the tip.

    Horizontal rather than a pie: these are nominal categories being compared,
    and a pie only works part-to-whole at a glance with few segments.
    """
    if not rows:
        return f'<p class="chart-empty">{_e(caption)}</p>'

    # One category is not a chart. A lone bar encodes a number against a scale
    # nobody can read, so the number says it instead — which is also the honest
    # reading of this demo: every receipt so far ended the same way.
    if len(rows) == 1:
        label, count = rows[0]
        return (
            f'<div class="tile" style="border:0;padding:0">'
            f'<div class="v">{count:,}</div>'
            f'<div class="s">all of them <code>{_e(label)}</code></div></div>'
        )

    width = 640
    row_h, gap = 30, 2
    label_w, value_w = 140, 46
    height = len(rows) * row_h
    top = _nice_ceiling(max(v for _, v in rows))
    track = width - label_w - value_w

    marks: list[str] = []
    for i, (label, count) in enumerate(rows):
        w = max(count * track // top if top else 0, 2)
        y = i * row_h + (row_h - _BAR_MAX) // 2 + gap // 2
        h = _BAR_MAX - gap
        text_y = y + h // 2 + 3
        marks.append(
            f'<text class="tick" x="{label_w - 10}" y="{text_y}" text-anchor="end">{_e(label)}</text>'
            f'<path class="mark" d="{_bar_path(label_w, y, w, h)}">'
            f"<title>{_e(label)}: {count}</title></path>"
            f'<text class="val" x="{label_w + w + 8}" y="{text_y}">{count}</text>'
        )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_e(caption)}">{"".join(marks)}</svg>'
    )


def _tile(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="s">{_e(sub)}</div>' if sub else ""
    return (
        f'<div class="tile"><div class="k">{_e(label)}</div>'
        f'<div class="v">{_e(value)}</div>{sub_html}</div>'
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
    refund_requests: list[dict[str, Any]]
    overdue_holds: list[dict[str, Any]]
    dev_profile_hosts: tuple[str, ...] = ()
    export_acknowledged: bool = True
    #: The public documents, as served. Keyed by path so the tab can print the
    #: URL an agent actually fetches rather than a name the console invented.
    discovery: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)


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
<div class="shell">
<div class="side">
<div class="brand">
<b>OpenStore</b>
<span>console</span>
</div>
<nav>{_nav(tab)}</nav>
</div>
<main>
<header class="topbar">
<div class="who">
<span class="eyebrow">Merchant</span>
<b>{_e(state.merchant_domain)}</b>
</div>
{_status(state)}
</header>
<div class="page-head"><h1>{_e(dict(TABS)[tab])}</h1><p>{_e(_SUBTITLES.get(tab, ""))}</p></div>
{_banners(state)}
{body}
</main>
</div>
</body>
</html>"""


def _nav(current: str) -> str:
    out = []
    for group, tabs in TAB_GROUPS:
        links = "".join(_tab_link(name, label, current) for name, label in tabs)
        out.append(f'<div class="nav-group"><span class="nav-head">{_e(group)}</span>{links}</div>')
    return "".join(out)


def _tab_link(name: str, label: str, current: str) -> str:
    marker = ' aria-current="page"' if name == current else ""
    return f'<a href="/agentic/{name}"{marker}>{_e(label)}</a>'


def _status(state: ConsoleState) -> str:
    """The three facts an operator checks before reading anything else: can this
    deployment sign, what rail is it on, and is it pretending to be live.

    Derived from the same state the tabs render, never a second source. A status
    strip that agreed with nothing else on the page would be worse than none.
    """
    live = [k for k in state.keys if not k.get("revoked_at")]
    signing = (
        f"{len(live)} signing key{'s' if len(live) != 1 else ''}"
        if live
        else "no signing key — nothing can be sealed"
    )
    chips = [
        f'<span class="chip{"" if live else " chip-warn"}">{_e(signing)}</span>',
        f'<span class="chip">rail: {_e(state.provider.get("adapter", "unknown"))}</span>',
        f'<span class="chip">{len(state.receipts)} sealed</span>',
    ]
    return f'<div class="status">{"".join(chips)}</div>'


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


def _overview(state: ConsoleState) -> str:
    """What agent-originated trade actually did, before the detail tabs.

    Every number here is derived from receipts this sidecar SEALED. Nothing is
    modelled, projected or smoothed: a demo with four receipts draws four
    receipts, because a dashboard that flatters a thin dataset is the one thing
    an operator can never un-learn to distrust.
    """
    receipts = state.receipts
    gmv = sum(int(r.get("total_minor") or 0) for r in receipts)

    # Sealings per day across a fortnight. Days with nothing keep their slot:
    # dropping empty days would compress the axis and turn a quiet week into a
    # busy one.
    from collections import Counter
    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).date()
    window = [today - timedelta(days=i) for i in range(13, -1, -1)]
    value_by_day: Counter[str] = Counter()
    for r in receipts:
        stamp = str(r.get("sealed_at") or "")[:10]
        if stamp:
            value_by_day[stamp] += int(r.get("total_minor") or 0)

    value_series: list[tuple[str, int]] = [
        (d.strftime("%d %b"), int(value_by_day.get(d.isoformat(), 0))) for d in window
    ]
    authority = Counter(str(r.get("authority") or "unknown") for r in receipts)
    authority_rows = sorted(authority.items(), key=lambda kv: (-kv[1], kv[0]))

    recent = "".join(
        f'<tr><td><a href="/receipt/{_e(r["receipt_id"])}">{_e(r["receipt_id"])}</a></td>'
        f'<td class="num">{_e(format_rupees(r["total_minor"]))}</td>'
        f"<td>{_e(r['authority'])}</td>"
        f'<td class="muted">{_e(str(r.get("sealed_at") or "")[:16].replace("T", " "))}</td></tr>'
        for r in receipts[:8]
    )

    open_refunds = sum(1 for r in state.refund_requests if r.get("state") == "requested")

    return f"""<div class="tiles">
{_tile("Receipts sealed", f"{len(receipts):,}", "signed and hash-chained")}
{_tile("Agentic GMV", format_rupees(gmv), "tax-inclusive, as quoted")}
{_tile("Agents registered", f"{len(state.agents):,}", "reputation buys throughput only")}
{_tile("Refunds open", f"{open_refunds:,}", "an agent may ask, never move money")}
</div>

<section>
<h2>Agentic sales, last 14 days</h2>
{_columns(value_series, format_rupees, "No receipts sealed in the last 14 days.")}
<p class="note muted">Value of every basket this sidecar sealed, by the day it was sealed.
A day with no column had no agent-originated order, not a missing reading.</p>
</section>

<div class="two-up">
<section>
<h2>How each purchase was approved</h2>
{_bars(authority_rows, "Nothing sealed yet, so no authority has been exercised.")}
<p class="note muted">Every receipt names the kind of approval that ended it. No bar here is
an agent acting alone, because there is no such receipt to draw.</p>
</section>

<section>
<h2>Latest sealed</h2>
<table><thead><tr><th>receipt</th><th class="num">total</th><th>authority</th><th>sealed</th></tr></thead>
<tbody>{recent or '<tr><td colspan="4" class="muted">No sealed receipts yet.</td></tr>'}</tbody></table>
</section>
</div>"""


def _keys(state: ConsoleState) -> str:
    rows = "".join(
        f"<tr><td>{_e(k['kid'])}</td><td>{_e(k['created_at'])}</td>"
        f"<td>{'<span class="warn">revoked ' + _e(k['revoked_at']) + '</span>' if k.get('revoked_at') else '<span class="ok">active</span>'}</td></tr>"
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
<tr><th>Per-order cap</th><td class="num">{_e(format_rupees(p["per_order_cap_minor"]))}</td></tr>
<tr><th>Per-order line count</th><td class="num">{_e(p["per_order_line_count"])}</td></tr>
<tr><th>Per-group quantity</th><td class="num">{_e(p["per_group_qty"])}</td></tr>
<tr><th>Blocked tags</th><td>{_e(", ".join(p["blocked_tags"]) or "—")}</td></tr>
<tr><th>Agent window</th><td>{"open" if p["window_open"] else '<span class="warn">closed</span>'}</td></tr>
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
<tr><th>Adapter</th><td>{_e(p["adapter"])}</td></tr>
<tr><th>Declared methods</th><td>{_e(", ".join(p["declared_methods"]))}</td></tr>
<tr><th>Enabled methods</th><td>{_e(", ".join(p["enabled_methods"]))}</td></tr>
<tr><th>Link lifetime</th><td class="num">{_e(p["link_lifetime_minutes"])} min</td></tr>
<tr><th>Webhooks</th><td>{_e(p["webhook_status"])}</td></tr>
</tbody></table>
<p class="note muted">Anything outside the enabled set refuses <code>method-not-supported</code>
naming what <em>is</em> enabled. The adapter declares what it can do; you decide what it may.</p>
</section>"""


def _authority(state: ConsoleState) -> str:
    a = state.authority
    return f"""<section>
<h2>Authority kinds accepted</h2>
<table><tbody>
<tr><th>Kinds</th><td>{_e(", ".join(a["kinds"]))}</td></tr>
<tr><th>confirmed-intent mechanisms</th><td>{_e(", ".join(a["mechanisms"]))}</td></tr>
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
        f"<tr><td>{_e(a.get('name') or '—')}<br>"
        # Truncated because an agent_id is a 43-character thumbprint and the
        # Merchant recognises an agent by what it does, not by its hash.
        f'<code class="muted">{_e(str(a["agent_id"])[:16])}…</code></td>'
        f'<td>{_e(a["tier"])}</td><td class="num">{_e(a.get("calls", 0))}</td>'
        f"<td>{_e(a.get('last_seen', '—'))}</td>"
        f"<td>{'<span class="warn">blocked</span>' if a.get('blocked') else '<span class="ok">allowed</span>'}</td></tr>"
        for a in state.agents
    )
    return f"""<section>
<h2>Agents</h2>
<table><thead><tr><th>agent</th><th>tier</th><th>calls</th><th>last seen</th><th>state</th></tr></thead>
<tbody>{rows or '<tr><td colspan="5" class="muted">No agents have registered yet.</td></tr>'}</tbody></table>
<p class="note muted">Reputation buys throughput only. An allowlisted agent gets a higher rate
limit and not one capability more — <code>confirm</code> without a fresh Authority is refused
either way.</p>
</section>"""


def _receipts(state: ConsoleState) -> str:
    rows = "".join(
        f'<tr><td><a href="/receipt/{_e(r["receipt_id"])}">{_e(r["receipt_id"])}</a></td>'
        f'<td class="num">{_e(format_rupees(r["total_minor"]))}</td>'
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


def _refunds(state: ConsoleState) -> str:
    """What agents have asked for, and what the shop did about it."""
    rows = "".join(
        f"<tr><td><code>{_e(r['order_id'])}</code></td>"
        f"<td>{_e(r['reason'] or '—')}</td>"
        f'<td><code class="muted">{_e(str(r["agent_id"])[:16])}…</code></td>'
        f"<td>{_e(r['requested_at'])}</td>"
        + (
            '<td><span class="warn">open</span></td>'
            if r["state"] == "requested"
            else f'<td><span class="ok">{_e(r["state"])}</span>'
            + (
                f'<br><span class="muted">{_e(r["resolution_note"])}</span>'
                if r["resolution_note"]
                else ""
            )
            + "</td>"
        )
        + "</tr>"
        for r in state.refund_requests
    )
    return f"""<section>
<h2>Refund requests</h2>
<table><thead><tr><th>order</th><th>reason given</th><th>asked by</th><th>when</th><th>state</th></tr></thead>
<tbody>{rows or '<tr><td colspan="5" class="muted">No agent has asked for a refund.</td></tr>'}</tbody></table>
<p class="note muted">An agent can ask and nothing more. The money moves when the shop's own
admin refunds the order, which closes the request here — an agent that could refund could move
money out of a shop it holds no credential for.</p>
</section>"""


def _health(state: ConsoleState) -> str:
    rows = "".join(
        f"<tr><td>{_e(h['order_id'])}</td><td>{_e(h['status'])}</td>"
        f'<td>{_e(h["deadline"])}</td><td class="num">{_e(format_rupees(h["amount_minor"]))}</td></tr>'
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


def _discovery(state: ConsoleState) -> str:
    """The public documents and the tool surface, rendered.

    **A second rendering of the same bytes, never a second document.** These are
    read from the very functions that serve `/.well-known/*` and `tools/list`,
    so this tab cannot drift from what an agent is handed. The alternative —
    content-negotiating the real paths on `Accept` — would make the bytes a
    Merchant inspected differ from the bytes an agent received, decided by a
    header and a `Vary` that some proxy in the middle may not honour.
    """
    docs = []
    for path, document in state.discovery.items():
        body = json.dumps(document, indent=2, sort_keys=True)
        docs.append(
            f"<section><h2>{_e(path)}</h2>"
            f'<p class="note muted">Public and unauthenticated by design — a card behind a '
            f"login is a card no new agent can read. "
            f'<a href="{_e(path)}">Open the raw document</a></p>'
            f"<details><summary>Show the bytes as served</summary>"
            f"<pre>{_e(body)}</pre></details></section>"
        )

    rows = []
    for tool in state.tools:
        annotations = tool.get("annotations", {})
        money = annotations.get("moneyPathHint")
        flags = []
        if annotations.get("readOnlyHint"):
            flags.append('<span class="ok">read-only</span>')
        if annotations.get("destructiveHint"):
            flags.append('<span class="warn">destructive</span>')
        rows.append(
            f'<tr><td><code>{_e(tool["name"])}</code></td>'
            f'<td>{_e(annotations.get("scope", ""))}</td>'
            f'<td>{"<span class=\"warn\">yes</span>" if money else "no"}</td>'
            f"<td>{' · '.join(flags) or '—'}</td></tr>"
        )
    tools_table = f"""<section>
<h2>Tool surface</h2>
<table><thead><tr><th>tool</th><th>scope</th><th>money path</th><th>hints</th></tr></thead>
<tbody>{"".join(rows) or '<tr><td colspan="4" class="muted">No tools exposed.</td></tr>'}</tbody></table>
<p class="note muted"><code>moneyPathHint</code> is this sidecar's own annotation, not a
standard MCP one. It is what lets a client offer standing approval for everything except the
scopes that move toward a spend — a permission UI that has to hardcode tool names is a
permission UI that breaks on the next tool.</p>
</section>"""

    return "".join(docs) + tools_table


_TABS = {
    "overview": _overview,
    "keys": _keys,
    "policy": _policy,
    "provider": _provider,
    "authority": _authority,
    "exposure": _exposure,
    "agents": _agents,
    "discovery": _discovery,
    "receipts": _receipts,
    "refunds": _refunds,
    "health": _health,
}
