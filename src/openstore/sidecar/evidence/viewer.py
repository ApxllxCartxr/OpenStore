"""The public receipt viewer — a document a Consumer can read, and print.

`SPECS/PLAN.md §512` asked for this and what shipped was `JSONResponse`. The one
route in the system built for a non-technical human was the one route that
rendered for machines, while `/agentic/approve` links a Consumer straight to it.

**A document, not a console page.** `/agentic` is an instrument panel — data
density, nine tabs, read at 2am by an operator. This is read once, by somebody
who just spent money, and it has one job: say what was bought, what kind of
approval happened, and whether the evidence still holds. So it is one column at
a reading measure on a sheet, and it prints.

The verdict is rendered from the same `verify()` the CLI calls. Nothing here
re-checks anything: a viewer that computed its own opinion of a receipt would be
a second verifier, and the interesting failure is the one where the two disagree
and the pretty one is believed.
"""

from __future__ import annotations

import html
from typing import Any

from openstore.sidecar.evidence.bundle import Bundle, format_rupees
from openstore.sidecar.verify.checks import ExitCode, VerifyResult

#: What each exit code means, in the words a Consumer needs rather than the
#: enum's. The CLI prints the name; a person reading their own receipt needs the
#: consequence.
_VERDICT: dict[ExitCode, tuple[str, str]] = {
    ExitCode.VALID: (
        "Intact",
        "Every section is unaltered, the chain holds, and the signature matches a key "
        "this receipt carries with it.",
    ),
    ExitCode.TAMPERED: (
        "Altered",
        "Something in this receipt does not match what was sealed. The failing check is "
        "named below — treat the contents as unproven.",
    ),
    ExitCode.UNTRUSTED_KEY: (
        "Untrusted key",
        "The chain is intact, but the key that signed this is not one the receipt's own "
        "key list vouches for.",
    ),
}

#: The five sections, in seal order, with what each one is *for*. The payload
#: keys are the system's words; these are the reader's.
#: Payment methods as a reader writes them. `upi` on a printed receipt reads as
#: a database value, which is what it is.
_METHOD_LABEL: dict[str, str] = {
    "upi": "UPI",
    "cash-on-delivery": "cash on delivery",
    "card": "card",
    "netbanking": "netbanking",
}

_SECTION_COPY: dict[str, tuple[str, str]] = {
    "bought": ("Bought", "the basket and the priced quote, verbatim"),
    "tapped": ("Approved", "which kind of human approval happened, and what it bound"),
    "decided": ("Decided", "the Gate's transcript for this order"),
    "told": ("Told", "what was sent, and when"),
    "moved": ("Moved", "the money events on this order"),
}

VIEWER_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg-sunken);
  color: var(--fg);
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 1.55;
  font-variant-numeric: tabular-nums;
}

/* The sheet. A receipt is a document, so it gets a page on a ground rather
   than the console's grid of cards. */
.sheet {
  max-width: 47rem;
  margin: 0 auto;
  background: var(--bg-raised);
  border-left: 1px solid var(--line);
  border-right: 1px solid var(--line);
  min-height: 100vh;
  padding: 32px 24px 56px;
}

/* Hashes, ids and signatures are compared character by character, so they get
   a real monospace stack. `--font-mono` in the token layer resolves to the
   sans, which is correct for the console's tabular figures and wrong for a
   64-character digest somebody is checking against another screen. */
.hash, code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.875em;
  word-break: break-all;
}

/* ── Masthead ── */
.mast { border-bottom: 2px solid var(--fg); padding-bottom: 14px; margin-bottom: 22px; }
.mast .shop {
  font-family: var(--font-display);
  font-size: 34px; line-height: 1.1; margin: 0; font-weight: 400;
}
.mast .kind {
  text-transform: uppercase; letter-spacing: 0.14em; font-size: 13px;
  color: var(--muted); margin: 0 0 6px;
}
.mast .id { margin: 10px 0 0; color: var(--muted); font-size: 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.chip {
  border: 1px solid var(--line); border-radius: 999px;
  padding: 2px 10px; font-size: 13px; color: var(--muted);
  background: var(--bg);
}
.chip.demo { border-color: var(--accent); color: var(--accent); }

/* ── Verdict ── */
.verdict {
  border: 1px solid var(--line);
  border-left: 3px solid var(--ok);
  border-radius: var(--radius-lg);
  background: var(--bg);
  padding: 16px 18px;
  margin-bottom: 26px;
}
.verdict.bad { border-left-color: var(--accent); }
.verdict h2 { margin: 0; font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }
.verdict.good h2 { color: var(--ok); }
.verdict.bad h2 { color: var(--accent); }
.verdict p { margin: 4px 0 0; color: var(--muted); max-width: 62ch; }

.checks { list-style: none; margin: 14px 0 0; padding: 0; }
.checks li { display: flex; gap: 10px; padding: 5px 0; align-items: baseline; }
.checks li + li { border-top: 1px solid var(--line); }
/* The mark is a glyph, not a colour alone: a red/green dot is the whole signal
   for a reader who cannot separate the two. */
.checks .mark { flex: none; width: 1.1rem; font-weight: 600; }
.checks .pass .mark { color: var(--ok); }
.checks .fail .mark { color: var(--accent); }
.checks .what { font-weight: 500; }
.checks .why { color: var(--muted); display: block; font-size: 14px; }

/* ── Sections ── */
section { margin: 0 0 28px; }
h3 {
  font-size: 13px; text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--muted); font-weight: 600;
  margin: 0 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--line);
}
.note { color: var(--muted); font-size: 14px; max-width: 64ch; }
.role { color: var(--muted); font-size: 14px; }

/* The pass/fail glyph is decorative to a screen reader, so the state is also
   written out in text that only a screen reader reaches. */
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
}

/* The claim. This is the sentence the whole product exists to be able to
   print, so it is set as prose at display size and not as a table row. */
.claim { font-size: 20px; line-height: 1.4; margin: 0 0 12px; max-width: 54ch; }
.claim b { font-weight: 600; }
dl.facts { display: grid; grid-template-columns: 9.5rem 1fr; gap: 6px 16px; margin: 0; }
dl.facts dt { color: var(--muted); font-size: 14px; }
dl.facts dd { margin: 0; }

table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); }
th { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
td.num, th.num { text-align: right; }
tbody tr:last-child td { border-bottom: 1px solid var(--line); }

/* The totals ladder. Right-aligned against the table above it, so the eye
   runs down one column to the number that was actually approved. */
.ladder { width: 100%; margin-top: 2px; }
.ladder td { border: 0; padding: 3px 8px; }
.ladder td.k { color: var(--muted); text-align: right; }
.ladder td.v { text-align: right; width: 8.5rem; }
.ladder tr.total td { border-top: 1px solid var(--fg); padding-top: 8px; font-size: 19px;
                      font-weight: 600; }
.ladder tr.total td.k { color: var(--fg); }

/* ── Chain ── */
.chain { list-style: none; margin: 0; padding: 0; }
.chain li { display: grid; grid-template-columns: 1.4rem 1fr; gap: 10px; }
.chain .rail { position: relative; }
/* One hairline through the dots. The chain is the claim here — five boxes with
   no line between them would draw five facts instead of one sequence. */
.chain .rail::before {
  content: ""; position: absolute; left: 50%; top: 0; bottom: 0;
  width: 1px; background: var(--line);
}
.chain li:first-child .rail::before { top: 12px; }
.chain li:last-child .rail::before { bottom: calc(100% - 12px); }
.chain .dot {
  position: relative; display: block; width: 7px; height: 7px; margin: 9px auto 0;
  border-radius: 50%; background: var(--accent-2);
}
.chain .body { padding-bottom: 14px; min-width: 0; }
.chain .name { font-weight: 600; }
.chain .role { color: var(--muted); font-size: 14px; }
.chain .link { color: var(--comment); font-size: 13px; }

/* ── Verify-it-yourself ── */
.self {
  background: var(--bg); border: 1px solid var(--line);
  border-radius: var(--radius-lg); padding: 16px 18px;
}
.self h3 { border: 0; padding: 0; }
pre {
  background: var(--bg-sunken); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 10px 12px; overflow-x: auto; margin: 0 0 10px;
}
a { color: var(--accent-2); }
a:focus-visible { outline: 2px solid var(--accent-2); outline-offset: 2px; }

.foot { margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--line);
        color: var(--muted); font-size: 14px; }

@media (max-width: 34rem) {
  .sheet { padding: 22px 16px 40px; }
  dl.facts { grid-template-columns: 1fr; gap: 2px 0; }
  dl.facts dd { margin-bottom: 8px; }
}

/* Printed, this is somebody's proof of purchase. Drop the ground and the
   sheet's borders, keep every fact, and never break a section across pages. */
@media print {
  body { background: #fff; }
  .sheet { max-width: none; border: 0; min-height: 0; padding: 0; }
  .verdict, .self { background: none; }
  section, .verdict, .checks li { break-inside: avoid; }
  .no-print { display: none; }
}
@media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
"""


def _e(value: Any) -> str:
    return html.escape(str(value))


def _shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{_e(title)}</title>
<link rel="stylesheet" href="/agentic/static/tokens.css">
<style>{VIEWER_CSS}</style>
</head>
<body>
<div class="sheet">
{body}
</div>
</body>
</html>"""


def not_found_page() -> str:
    """The 404.

    **Says nothing about why.** A missing receipt and a wrong id answer
    identically, because the difference between them is exactly the oracle the
    unguessable id exists to close — and a friendlier page that distinguished
    "expired" from "never existed" would hand a guesser a confirmation channel
    that the JSON response was careful not to give.
    """
    return _shell(
        "Receipt not found",
        '<div class="mast"><p class="kind">Receipt</p>'
        '<h1 class="shop">Not found</h1></div>'
        '<p class="note">No receipt is available at this link. Check that you have the '
        "whole address — a receipt id is long, and a link broken across two lines in an "
        "email is the usual cause.</p>",
    )


# ── Pieces ───────────────────────────────────────────────────────────────────


def _masthead(bundle: Bundle) -> str:
    chips = [f'<span class="chip">v{_e(bundle.version)}</span>']
    if bundle.demo:
        chips.append('<span class="chip demo">Demo — no real money moved</span>')
    sealed = f"Sealed {_e(bundle.sealed_at)}" if bundle.sealed_at else "Sealed"
    return (
        '<div class="mast">'
        '<p class="kind">Receipt</p>'
        f'<h1 class="shop">{_e(bundle.merchant_domain)}</h1>'
        f'<p class="id">{sealed} · <span class="hash">{_e(bundle.receipt_id)}</span></p>'
        f'<div class="chips">{"".join(chips)}</div>'
        "</div>"
    )


def _verdict(result: VerifyResult) -> str:
    headline, meaning = _VERDICT[result.exit_code]
    tone = "good" if result.ok else "bad"
    checks = []
    for finding in result.findings:
        mark = "✓" if finding.ok else "✕"
        state = "pass" if finding.ok else "fail"
        why = f'<span class="why">{_e(finding.detail)}</span>' if finding.detail else ""
        checks.append(
            f'<li class="{state}"><span class="mark" aria-hidden="true">{mark}</span>'
            f'<span><span class="what">{_e(finding.label)}</span>'
            f'<span class="sr-only"> — {"passed" if finding.ok else "failed"}</span>'
            f"{why}</span></li>"
        )
    unopened = ""
    if result.unopened:
        names = ", ".join(_e(u) for u in result.unopened)
        unopened = (
            f'<p class="note" style="margin-top:12px">Not checked here: <b>{names}</b>. '
            "Your address and contact details are committed to rather than stored in the "
            "signed receipt, so they can only be opened with the shop's salt — and deleting "
            "them must never break the rest of this page.</p>"
        )
    return (
        f'<div class="verdict {tone}">'
        f"<h2>{_e(headline)}</h2><p>{_e(meaning)}</p>"
        f'<ul class="checks">{"".join(checks)}</ul>'
        f"{unopened}</div>"
    )


def _claim(claims: dict[str, str]) -> str:
    """The authority claim, as a sentence.

    Never "verified" unqualified (ADR-0017). The kind, the mechanism and what it
    bound are three separate facts and the reader gets all three, because a
    receipt that flattens them is a receipt that overstates the weakest rail.
    """
    kind = claims.get("authority", "")
    if not kind:
        # `verify()` returns early on a broken chain, before it reads the claims.
        # Printing "Approved by —" there would state an absence as a fact; the
        # honest line is that the check never got far enough to read it.
        return (
            "<section><h3>How this was approved</h3>"
            '<p class="note">Not shown. Verification stopped at the failing check above, '
            "and the approval claim is only read from a receipt whose chain holds.</p></section>"
        )
    binding = claims.get("binding", "")
    sentence = f"Approved by <b>{_e(kind)}</b>"
    if binding:
        sentence += f", {_e(binding)}"
    sentence += "."

    rows = []
    for key in ("mechanism", "ceremony", "strength"):
        if claims.get(key):
            rows.append(f"<dt>{_e(key.capitalize())}</dt><dd>{_e(claims[key])}</dd>")
    for key, value in sorted(claims.items()):
        if key not in ("receipt_id", "authority", "binding", "mechanism", "ceremony", "strength"):
            rows.append(f"<dt>{_e(key.capitalize())}</dt><dd>{_e(value)}</dd>")
    facts = f'<dl class="facts">{"".join(rows)}</dl>' if rows else ""
    return (
        "<section><h3>How this was approved</h3>"
        f'<p class="claim">{sentence}</p>{facts}</section>'
    )


def _rate(bp: Any) -> str:
    """Basis points as a percentage, by integer arithmetic. 1800 → `18%`,
    250 → `2.5%`."""
    if not isinstance(bp, int):
        return ""
    whole, frac = divmod(bp, 100)
    return f"{whole}%" if frac == 0 else f"{whole}.{frac:02d}".rstrip("0") + "%"


def _bought(bundle: Bundle, verified: bool) -> str:
    payload = bundle.section("bought").payload
    quote: dict[str, Any] = payload.get("quote") or {}
    # The priced rows live on the Quote. `lines` on the section is the basket —
    # sku and qty — and pricing it here would be a second money path.
    rows: list[dict[str, Any]] = quote.get("lines") or payload.get("lines") or []

    body = []
    for line in rows:
        qty = line.get("qty", "")
        unit = line.get("unit_price_minor")
        total = line.get("line_total_minor")
        tax_note = _rate(line.get("gst_rate_bp"))
        hsn = line.get("hsn_sac", "")
        sub = " · ".join(p for p in (f"HSN {hsn}" if hsn else "", tax_note) if p)
        body.append(
            f'<tr><td><span class="hash">{_e(line.get("sku", ""))}</span>'
            + (f'<br><span class="role">{_e(sub)}</span>' if sub else "")
            + f'</td><td class="num">{_e(qty)}</td>'
            f'<td class="num">{_e(format_rupees(unit)) if unit is not None else "—"}</td>'
            f'<td class="num">{_e(format_rupees(total)) if total is not None else "—"}</td></tr>'
        )

    table = (
        "<table><thead><tr><th>Item</th><th class='num'>Qty</th>"
        "<th class='num'>Unit</th><th class='num'>Amount</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        if body
        else '<p class="note">No priced lines on this receipt.</p>'
    )

    ladder = []
    if "subtotal_minor" in quote:
        ladder.append(("Subtotal", quote["subtotal_minor"]))
    for discount in quote.get("discount_lines") or []:
        label = discount.get("label") or discount.get("code", "Discount")
        ladder.append((str(label), discount.get("amount_minor", 0)))
    chosen = quote.get("fulfillment_chosen") or {}
    if chosen:
        ladder.append(("Delivery", chosen.get("cost_minor", 0)))
    for tax in quote.get("tax_lines") or []:
        label = tax.get("label") or tax.get("kind", "Tax")
        rate = _rate(tax.get("rate_bp"))
        ladder.append((f"{label} {rate}".strip(), tax.get("amount_minor", 0)))
    if quote.get("round_off_minor"):
        ladder.append(("Rounding", quote["round_off_minor"]))

    ladder_rows = "".join(
        f'<tr><td class="k">{_e(label)}</td><td class="v">{_e(format_rupees(amount))}</td></tr>'
        for label, amount in ladder
        if isinstance(amount, int)
    )
    if "total_minor" in quote:
        ladder_rows += (
            '<tr class="total"><td class="k">Total approved</td>'
            f'<td class="v">{_e(format_rupees(quote["total_minor"]))}</td></tr>'
        )
    totals = f'<table class="ladder">{ladder_rows}</table>' if ladder_rows else ""

    inclusive = ""
    if quote.get("tax_inclusive"):
        inclusive = '<p class="note">Prices shown include GST.</p>'

    # Drawn from the bundle either way, because hiding the figures on a failed
    # verdict would hide the evidence of what was altered. Labelled, because a
    # table that looks identical on both verdicts is the one way this page could
    # mislead.
    unproven = (
        '<p class="note"><b>Unverified.</b> These figures are what the receipt now says, '
        "not what was proven to have been sealed.</p>"
        if not verified
        else ""
    )

    return f"<section><h3>What was bought</h3>{table}{totals}{inclusive}{unproven}</section>"


def _moved(bundle: Bundle) -> str:
    payload = bundle.section("moved").payload
    entries: list[dict[str, Any]] = payload.get("entries") or []
    method = payload.get("method", "")

    if not entries:
        note = (
            "No money has moved yet. On cash on delivery the receipt is sealed when the "
            "order is confirmed, so you hold verifiable evidence before you pay rather "
            "than only after."
        )
        rows = f'<p class="note">{note}</p>'
    else:
        body = "".join(
            f'<tr><td>{_e(entry.get("kind", ""))}</td>'
            f'<td class="num">{_e(format_rupees(entry.get("amount_minor", 0)))}</td>'
            f'<td><span class="role">{_e(entry.get("at", ""))}</span></td>'
            f'<td><span class="hash">{_e(entry.get("reference", ""))}</span></td></tr>'
            for entry in entries
        )
        rows = (
            "<table><thead><tr><th>Event</th><th class='num'>Amount</th>"
            f"<th>When</th><th>Reference</th></tr></thead><tbody>{body}</tbody></table>"
        )

    asserted = ""
    if payload.get("merchant_asserted"):
        asserted = (
            '<p class="note"><b>Merchant-asserted.</b> Cash was collected in person, so no '
            "payment rail attested this — the shop says it happened and nothing independent "
            "confirms it. That is stated rather than hidden because the alternative is a "
            "receipt that reads stronger than the evidence behind it.</p>"
        )
    label = _METHOD_LABEL.get(str(method), str(method))
    method_note = f'<p class="note">Paid by {_e(label)}.</p>' if method and entries else ""
    return f"<section><h3>What moved</h3>{rows}{method_note}{asserted}</section>"


def _chain(bundle: Bundle) -> str:
    items = []
    for section in bundle.sections:
        name, role = _SECTION_COPY.get(section.name, (section.name, ""))
        items.append(
            '<li><span class="rail"><span class="dot"></span></span>'
            f'<span class="body"><span class="name">{_e(name)}</span> '
            f'<span class="role">— {_e(role)}</span><br>'
            f'<span class="link hash">{_e(section.link)}</span></span></li>'
        )
    signed = (
        f'<dl class="facts"><dt>Signed by</dt><dd><span class="hash">'
        f"{_e(bundle.signing_kid)}</span></dd>"
        f'<dt>Signature</dt><dd><span class="hash">{_e(bundle.signature)}</span></dd></dl>'
    )
    return (
        "<section><h3>The chain</h3>"
        f'<ul class="chain">{"".join(items)}</ul>'
        '<p class="note">Each section is hashed together with the one before it and with its '
        "own name, so a section cannot be edited, reordered, or presented as a different "
        "section without breaking every link after it.</p>"
        f"{signed}</section>"
    )


def _self_check(bundle: Bundle) -> str:
    """How to check this without trusting this page.

    The page is served by the sidecar whose behaviour is in question, so a green
    verdict here is worth exactly as much as the server rendering it. The
    receipt carries its own key snapshot precisely so the reader does not have
    to take that on faith.
    """
    return (
        '<section class="self"><h3>Check this yourself</h3>'
        '<p class="note">This page is drawn by the shop\'s own server. The JSON below verifies '
        "offline — it carries the keys it was signed under, so checking it needs no network "
        "and no cooperation from the shop.</p>"
        f"<pre><code>curl -O https://{_e(bundle.merchant_domain)}"
        f"/receipt/{_e(bundle.receipt_id)}.json\n"
        f"openstore-verify {_e(bundle.receipt_id)}.json</code></pre>"
        '<p class="note no-print">Exit <code>0</code> valid · <code>1</code> altered, naming the '
        "broken link · <code>2</code> untrusted key. "
        f'<a href="/receipt/{_e(bundle.receipt_id)}.json">Download the JSON</a></p></section>'
    )


def receipt_page(bundle: Bundle, result: VerifyResult) -> str:
    """The whole document."""
    body = "".join(
        [
            _masthead(bundle),
            _verdict(result),
            _claim(result.claims),
            _bought(bundle, result.ok),
            _moved(bundle),
            _chain(bundle),
            _self_check(bundle),
            '<p class="foot">Keep this link. The id in it is the only credential — anyone '
            "holding it can read this receipt, and nobody without it can.</p>",
        ]
    )
    return _shell(f"Receipt {bundle.receipt_id} — {bundle.merchant_domain}", body)


__all__ = ["VIEWER_CSS", "not_found_page", "receipt_page"]
