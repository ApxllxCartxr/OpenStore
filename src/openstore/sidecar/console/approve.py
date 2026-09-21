"""`/agentic/approve` — the tap page. The one place a spend is authorized.

**Public inside an otherwise-authenticated prefix, deliberately.** It is
authenticated by the one-time tap token and *not* by the Merchant session: a
Consumer approving a spend is not the Merchant, and requiring the shop's login
would make the whole flow impossible. That is the kind of thing a proxy config
gets wrong once and serves wrong forever, so it is written down in A8's table
and asserted by tests.

What happens here, in order:

1. The token is checked — unspent, unexpired, and bound to this exact cart.
2. The page renders the Merchant-signed Quote **verbatim**, with a countdown.
3. An optional `Have a code?` field takes a **private** Discount Code, re-calls
   door 9, re-renders the new total, and **rebinds** before anything is signed —
   so the code never transits the agent (ADR-0015).
4. The Consumer authorizes: a UPI intent in their own PSP app by default, or a
   passkey tap where the Merchant enabled that.
5. Auto-return via a resume URL carrying an unguessable, single-use,
   session-bound token — never `order_id` and `chat_thread_id` as bare
   parameters, which would hand anyone with the link someone else's checkout.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from openstore.sidecar.authority.tokens import TokenStore
from openstore.sidecar.console.render import CONSOLE_CSS
from openstore.sidecar.core.codes import PaymentMethod, ReasonCode
from openstore.sidecar.evidence.bundle import format_rupees
from openstore.sidecar.trait.errors import TraitError

router = APIRouter(prefix="/agentic")


@dataclass
class ApproveContext:
    tokens: TokenStore = field(default_factory=TokenStore)
    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    merchant_domain: str = "spoiledduckie.localhost"
    merchant_name: str = "SpoiledDuckie"
    enabled_methods: frozenset[PaymentMethod] = frozenset(
        {PaymentMethod.UPI, PaymentMethod.CASH_ON_DELIVERY}
    )
    demo: bool = True


_context = ApproveContext()


def configure(context: ApproveContext) -> None:
    global _context
    _context = context


def get_context() -> ApproveContext:
    return _context


def _e(value: Any) -> str:
    return html.escape(str(value))


def render_approve(
    *,
    merchant_name: str,
    quote: dict[str, Any],
    token: str,
    expires_in_seconds: int,
    enabled_methods: frozenset[PaymentMethod],
    demo: bool,
) -> str:
    """The page of record.

    Every figure here comes from the Merchant-signed Quote. The sidecar renders
    it; it does not compute it. What the Consumer sees is what was signed, and a
    change after render invalidates the token even when the total did not move.
    """
    rows: list[str] = []
    for line in quote.get("lines", []):
        label = _e(line["sku"])
        if line.get("qty", 1) > 1:
            label += f" × {_e(line['qty'])}"
        addons = line.get("addons") or []
        note = (
            f'<div class="muted">includes {_e(", ".join(a["sku"] for a in addons))}</div>'
            if addons
            else ""
        )
        rows.append(
            f"<tr><td>{label}{note}</td>"
            f'<td class="num">{_e(format_rupees(line["line_total_minor"]))}</td></tr>'
        )

    for discount in quote.get("discount_lines", []):
        rows.append(
            f'<tr><td>{_e(discount["label"])} <span class="muted">{_e(discount["code"])}</span></td>'
            f'<td class="num">{_e(format_rupees(discount["amount_minor"]))}</td></tr>'
        )

    chosen = quote.get("fulfillment_chosen", {})
    option = next(
        (o for o in quote.get("fulfillment_options", []) if o["id"] == chosen.get("id")), None
    )
    rows.append(
        f"<tr><td>{_e(option['label'] if option else 'Delivery')}"
        + (f'<div class="muted">{_e(option["eta_days"])} days</div>' if option else "")
        + f'</td><td class="num">{_e(format_rupees(chosen.get("cost_minor", 0)))}</td></tr>'
    )

    for tax in quote.get("tax_lines", []):
        included = ' <span class="muted">included</span>' if tax.get("informational") else ""
        rows.append(
            f'<tr><td>{_e(tax["label"])}{included}</td>'
            f'<td class="num">{_e(format_rupees(tax["amount_minor"]))}</td></tr>'
        )

    def _method_label(method: PaymentMethod) -> str:
        return (
            "Cash on delivery" if method is PaymentMethod.CASH_ON_DELIVERY else method.value.upper()
        )

    # Only what this Merchant enabled. Offering a method that will be refused at
    # the Gate teaches the Consumer that the page lies.
    methods = "".join(
        f'<label class="method"><input type="radio" name="method" value="{_e(m.value)}"'
        + (" checked" if m is PaymentMethod.UPI else "")
        + f"> {_e(_method_label(m))}</label>"
        for m in sorted(enabled_methods, key=lambda m: m.value)
    )

    demo_banner = (
        "<div class='banner'><strong>Demo</strong> — no real money moves, and this receipt "
        "is marked as a demo receipt.</div>"
        if demo
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Approve — {_e(merchant_name)}</title>
<link rel="stylesheet" href="/agentic/static/tokens.css">
<style>{CONSOLE_CSS}
main {{ max-width: 34rem; margin: 0 auto; }}
.total td {{ font-weight: 600; border-top: 2px solid var(--line, #e2e0da); }}
.method {{ display: flex; align-items: center; gap: 0.5rem; min-height: 44px; }}
.tap {{ width: 100%; min-height: 52px; background: var(--accent, #c2415a); color: var(--paper, #f6f5f2);
       border: 0; font: inherit; font-weight: 600; cursor: pointer; }}
#countdown {{ font-variant-numeric: tabular-nums; }}
</style>
</head>
<body>
<header><h1>{_e(merchant_name)}</h1></header>
<main>
{demo_banner}
<section>
<h2>What you are approving</h2>
<table>
<tbody>
{"".join(rows)}
<tr class="total"><td>Total</td>
  <td class="num">{_e(format_rupees(quote.get("total_minor", 0)))}</td></tr>
</tbody>
</table>
<p class="note muted">These are the shop's own figures, signed by the shop. Nothing here was
calculated by the agent that built this basket.</p>
</section>

<form method="POST" action="/agentic/approve">
<input type="hidden" name="t" value="{_e(token)}">

<section>
<h2>Have a code?</h2>
<input name="code" placeholder="Private code" autocomplete="off"
  style="width:100%;min-height:44px;padding:0 0.75rem;border:1px solid var(--line,#e2e0da);background:var(--raised,#fffdfa);color:inherit">
<p class="note muted">A private code is entered here and never travels through the agent.
Applying one re-prices the basket and you will approve the new total.</p>
</section>

<section>
<h2>How you are paying</h2>
{methods}
</section>

<p class="note">This link is good for <span id="countdown">{_e(expires_in_seconds)}</span> seconds
and can be used once.</p>
<button class="tap" type="submit">Approve {_e(format_rupees(quote.get("total_minor", 0)))}</button>
</form>

<p class="note muted">The shop asked for this exact amount. Approving it here authorizes that
amount and nothing else — the agent never holds a payment credential.</p>
</main>
<script>
// A visible clock, because "this expires" with no number is a sentence nobody
// acts on. The server is the authority; this is only the display.
(function () {{
  var el = document.getElementById('countdown');
  var left = {expires_in_seconds};
  var timer = setInterval(function () {{
    left -= 1;
    if (left <= 0) {{ clearInterval(timer); el.textContent = '0'; return; }}
    el.textContent = String(left);
  }}, 1000);
}})();
</script>
</body>
</html>"""


# `response_model=None`: this returns HTML on the happy path and a JSON refusal
# envelope otherwise, and FastAPI cannot build one response model from both.
@router.get("/approve", response_class=HTMLResponse, response_model=None)
def approve(request: Request) -> HTMLResponse | JSONResponse:
    token = request.query_params.get("t", "")
    record = _context.tokens.taps.get(token)
    if record is None:
        error = TraitError(ReasonCode.NOT_FOUND, "That approval link is not valid.")
        return JSONResponse(status_code=404, content=error.to_payload())
    if record.spent:
        error = TraitError(ReasonCode.AUTHORITY_STALE, "That approval link has already been used.")
        return JSONResponse(status_code=403, content=error.to_payload())

    quote = _context.quotes.get(record.order_id, {"total_minor": record.total_minor})
    from datetime import UTC, datetime

    remaining = max(0, int((record.expires_at - datetime.now(UTC)).total_seconds()))

    return HTMLResponse(
        render_approve(
            merchant_name=_context.merchant_name,
            quote=quote,
            token=token,
            expires_in_seconds=remaining,
            enabled_methods=_context.enabled_methods,
            demo=_context.demo,
        )
    )


# ── The tap ──────────────────────────────────────────────────────────────────
#
# The page above has rendered a `<form method="POST">` since it was written and
# there was nothing behind it, so the one button in the product 405'd. This is
# it: the only route in the sidecar that may permit a spend.


async def _form(request: Request) -> dict[str, str]:
    """Read an `application/x-www-form-urlencoded` body.

    Starlette's `request.form()` asserts `python-multipart` is installed even
    for urlencoded bodies. The approve form posts three short fields and no
    file, so this parses them directly rather than adding a dependency for a
    capability the page does not use.
    """
    raw = (await request.body()).decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def _page(title: str, body: str, *, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        status_code=status,
        content=(
            f"<!doctype html><html lang=en><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_e(title)} — {_e(_context.merchant_name)}</title>"
            f"<style>{CONSOLE_CSS}</style><main class=wrap>{body}</main></html>"
        ),
    )


@router.post("/approve", response_class=HTMLResponse, response_model=None)
async def approve_tap(request: Request) -> HTMLResponse:
    """Spend the tap token, run the Gate for real, and move to payment.

    A refusal renders the Gate's own reason. It is the Consumer's money and
    their time: "something went wrong" would be the one thing this page must
    never say.
    """
    from openstore.sidecar import checkout as flow

    form = await _form(request)
    token = form.get("t", "")
    chosen = form.get("method", "")
    ctx = flow.get_context()

    method = None
    if chosen:
        try:
            method = PaymentMethod(chosen)
        except ValueError:
            return _page(
                "Not a payment method",
                f"<h1>That is not a payment method</h1><p class=note>{_e(chosen)} is not one this "
                f"shop accepts.</p>",
                status=400,
            )

    try:
        result = await flow.tap(ctx, token, method=method)
    except flow.CheckoutRefused as refusal:
        return _page(
            "Not approved",
            f"<h1>This approval could not be used</h1>"
            f"<p class=note><code>{_e(refusal.code.value)}</code> — {_e(refusal.detail)}</p>"
            f"<p class=note muted>No money has moved and nothing is held.</p>",
            status=403,
        )

    if result.refused:
        assert result.reason_code is not None
        return _page(
            "Refused",
            f"<h1>The shop refused this order</h1>"
            f"<p class=note><code>{_e(result.reason_code.value)}</code> — {_e(result.detail)}</p>"
            f"<p class=note muted>No money has moved. The Gate checks the Merchant's own "
            f"prices and stock again at the moment you approve, which is why this can "
            f"differ from what the agent last saw.</p>",
            status=409,
        )

    if result.receipt_id:
        # Cash on delivery: authorized, nothing charged, receipt already sealed.
        return _page(
            "Order confirmed",
            f"<h1>Order confirmed</h1>"
            f"<p class=note>Paying cash on delivery. Nothing has been charged.</p>"
            f"<p><a href='/receipt/{_e(result.receipt_id)}'>Your receipt</a></p>",
        )

    return HTMLResponse(
        status_code=303,
        content="",
        headers={"location": result.pay_url},
    )


@router.get("/fake-pay/{link_id}", response_class=HTMLResponse, response_model=None)
def fake_pay(link_id: str) -> HTMLResponse:
    """The demo Provider's payment page.

    `FakeProvider.make_link` has always returned this URL and the route did not
    exist, so the money step dead-ended at a 404. It is deliberately ugly and
    deliberately says what it is: nothing here touches a rail.
    """
    from openstore.sidecar import checkout as flow

    ctx = flow.get_context()
    order_id = ctx.by_link.get(link_id, "")
    checkout = ctx.pending.get(order_id)
    if checkout is None:
        return _page("Unknown payment", "<h1>That payment link is not valid</h1>", status=404)

    return _page(
        "Demo payment",
        f"<h1>Demo payment</h1>"
        f"<p class=note>This is a <strong>fake rail</strong>. No money moves, and the receipt "
        f"you get will say so.</p>"
        f"<table><tr><th>Order</th><td><code>{_e(checkout.order_id)}</code></td></tr>"
        f"<tr><th>Amount</th><td class=num>{_e(format_rupees(checkout.total_minor))}</td></tr></table>"
        f"<form method=POST action='/agentic/fake-pay/{_e(link_id)}'>"
        f"<input type=hidden name=outcome value=paid>"
        f"<button class=tap type=submit>Pay {_e(format_rupees(checkout.total_minor))}</button>"
        f"</form>",
    )


@router.post("/fake-pay/{link_id}", response_class=HTMLResponse, response_model=None)
async def fake_pay_submit(link_id: str) -> HTMLResponse:
    """The money arrives. Settle it, commit the stock, seal the receipt."""
    from openstore.sidecar import checkout as flow

    ctx = flow.get_context()
    ctx.provider.approve(link_id)
    try:
        checkout = await flow.complete(ctx, link_id, payer_handle="demo@upi")
    except flow.CheckoutRefused as refusal:
        return _page(
            "Not settled",
            f"<h1>That payment did not settle</h1>"
            f"<p class=note><code>{_e(refusal.code.value)}</code> — {_e(refusal.detail)}</p>",
            status=409,
        )
    return HTMLResponse(
        status_code=303, content="", headers={"location": f"/receipt/{checkout.receipt_id}"}
    )
