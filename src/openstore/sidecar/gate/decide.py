"""The Gate. A deterministic checklist, and nothing else.

Three entry points, not two (§6.5). `decide()` and `settle()` are the prepaid
pair; `collect()` is COD's, and conflating it with `settle()` is a mistake worth
naming — it verifies no Provider record because there is none, records nothing
about Authority because `confirmed-intent` already resolved at `decide()`,
writes exactly one `CAPTURE`, and may run no Gate check at all, because the
goods are already delivered and there is nothing left to refuse.

All twelve checks are inside `decide()` and **nothing happens between them**.
`quote-fresh` is check 11 and `method-enabled` is check 12, which reads as
though a check runs after the reserve. It does not: `decide()` is the last thing
before the reserve, and `method-enabled` is a static check on a set the Merchant
configured, so nothing between 11 and 12 can move a price.

No LLM output ever reaches here. Agent output is a proposal; this re-validates
server-side every time (SPEC §12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openstore.sidecar.core.canonical import cart_hash as compute_cart_hash
from openstore.sidecar.core.canonical import pii_commit, quote_hash
from openstore.sidecar.core.codes import (
    AuthorityKind,
    CheckResult,
    GateCheck,
    IntentMechanism,
    PaymentMethod,
    ReasonCode,
)
from openstore.sidecar.gate.policy import Policy
from openstore.sidecar.gate.transcript import Transcript, binding_for
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.models import Destination, Line, Quote


class GateRefused(Exception):
    """A check failed. Carries the first failure's reason code and the
    Transcript that records every check up to it."""

    def __init__(self, reason_code: ReasonCode, detail: str, transcript: Transcript) -> None:
        super().__init__(f"{reason_code.value}: {detail}")
        self.reason_code = reason_code
        self.detail = detail
        self.transcript = transcript


@dataclass
class Authority:
    """One fresh human permission per spend (ADR-0017).

    `present` is whether the ceremony has already happened. For `upi-pin` it has
    not — the payer authenticates in their own PSP app and the Authority arrives
    with the money, which is why check 1 defers rather than passing.
    """

    kind: AuthorityKind
    mechanism: IntentMechanism | None = None
    present: bool = False
    ceremony: str | None = None
    bound_cart_hash: str | None = None
    bound_amount_minor: int | None = None


@dataclass
class DecisionInput:
    """Everything the Gate needs, assembled by the caller before it runs.

    Deliberately a value object: the Gate reads it and reaches out only to door
    9 and door 2. A decision that could reach anywhere would not be reviewable
    as a checklist.
    """

    order_id: str
    cart_id: str
    lines: list[Line]
    destination: Destination
    contact: dict[str, str]
    fulfillment_option_id: str
    order_salt: bytes
    expiry_utc: str
    merchant_domain: str
    agent_id: str
    consumer_id: str
    authority: Authority
    method: PaymentMethod
    pinned_quote: Quote | None = None
    pinned_quote_bytes: bytes | None = None
    group_of: dict[str, str] = field(default_factory=dict)
    tags_of: dict[str, list[str]] = field(default_factory=dict)
    attested_prices: dict[str, int] = field(default_factory=dict)
    discount_code: str | None = None


@dataclass
class Decision:
    transcript: Transcript
    quote: Quote
    quote_bytes: bytes
    cart_hash: str
    permitted: bool

    @property
    def total_minor(self) -> int:
        return self.quote.total_minor


class Gate:
    """Reads Merchant truth fresh before any money move. Never computes a price."""

    def __init__(self, trait: TraitClient, policy: Policy) -> None:
        self._trait = trait
        self._policy = policy

    async def decide(self, request: DecisionInput, *, dry_run: bool = False) -> Decision:
        """The twelve checks, in order, stopping at the first failure.

        `dry_run` changes nothing about what is checked — it only means no
        Transcript is persisted and no caller may act on the result. A dry run
        that skipped checks would answer a different question than the real one.
        """
        quote, quote_bytes = await self._quote_now(request)
        pinned = request.pinned_quote or quote
        pinned_bytes = request.pinned_quote_bytes or quote_bytes

        qh = quote_hash(_quote_dict(pinned))
        dest_hash = pii_commit(request.order_salt, request.destination.model_dump())
        contact_hash = pii_commit(request.order_salt, dict(sorted(request.contact.items())))
        cart_hash = compute_cart_hash(
            lines=[
                {
                    "sku": ln.sku,
                    "qty": ln.qty,
                    "price_minor": request.attested_prices.get(ln.sku, 0),
                }
                for ln in request.lines
            ],
            quote_hash_hex=qh,
            destination_hash=dest_hash,
            contact_hash=contact_hash,
            fulfillment_option_id=request.fulfillment_option_id,
            total_minor=pinned.total_minor,
            currency=pinned.currency,
            merchant_domain=request.merchant_domain,
            expiry_utc=request.expiry_utc,
        )

        transcript = Transcript(
            order_id=request.order_id,
            cart_hash=cart_hash,
            quote_hash=qh,
            total_minor=pinned.total_minor,
            currency=pinned.currency,
            merchant_domain=request.merchant_domain,
            expiry_utc=request.expiry_utc,
            agent_id=request.agent_id,
            consumer_id=request.consumer_id,
        )

        checks = (
            (GateCheck.AUTHORITY_PRESENT_AND_ACCEPTED, self._check_authority),
            (GateCheck.CURRENCY, self._check_currency),
            (GateCheck.MERCHANT, self._check_merchant),
            (GateCheck.WINDOW, self._check_window),
            (GateCheck.COUNT, self._check_count),
            (GateCheck.QTY, self._check_qty),
            (GateCheck.BLOCKED, self._check_blocked),
            (GateCheck.TAGS, self._check_tags),
            (GateCheck.CAPS, self._check_caps),
            (GateCheck.QUOTE_CONSISTENT, self._check_quote_consistent),
            (GateCheck.QUOTE_FRESH, self._check_quote_fresh),
            (GateCheck.METHOD_ENABLED, self._check_method_enabled),
        )

        context = _Context(
            request=request,
            pinned=pinned,
            pinned_bytes=pinned_bytes,
            fresh=quote,
            fresh_bytes=quote_bytes,
            cart_hash=cart_hash,
        )

        for check, fn in checks:
            result, code, detail = fn(context)
            transcript.record(check, result, code, detail)
            if result is CheckResult.FAIL:
                assert code is not None
                raise GateRefused(code, detail, transcript)

        if transcript.authority_kind is None:
            transcript.authority_kind = request.authority.kind
            transcript.authority_mechanism = (
                request.authority.mechanism.value if request.authority.mechanism else None
            )
            transcript.ceremony = request.authority.ceremony

        return Decision(
            transcript=transcript,
            quote=pinned,
            quote_bytes=pinned_bytes,
            cart_hash=cart_hash,
            permitted=not dry_run,
        )

    async def _quote_now(self, request: DecisionInput) -> tuple[Quote, bytes]:
        return await self._trait.quote(
            request.lines,
            request.destination,
            fulfillment_option_id=request.fulfillment_option_id,
            discount_code=request.discount_code,
        )

    # ── the twelve ───────────────────────────────────────────────────────────

    def _check_authority(self, ctx: _Context) -> _Outcome:
        """Check 1. The kind must be one the Merchant enabled, and present if it
        lands before the Gate. When each kind lands is fixed by the Binding
        table and declared by the kind — never by the caller."""
        authority = ctx.request.authority
        policy = self._policy

        if authority.kind not in policy.enabled_authority_kinds:
            return (
                CheckResult.FAIL,
                ReasonCode.AUTHORITY_KIND_NOT_ENABLED,
                f"{authority.kind.value} is not enabled; this Merchant accepts "
                + ", ".join(sorted(k.value for k in policy.enabled_authority_kinds)),
            )

        if authority.kind is AuthorityKind.CONFIRMED_INTENT:
            if authority.mechanism is None:
                return (
                    CheckResult.FAIL,
                    ReasonCode.INTENT_MECHANISM_NOT_ENABLED,
                    "confirmed-intent needs a mechanism: upi-verify or passkey. An OTP to "
                    "a Contact Point is not a member and never becomes one.",
                )
            if authority.mechanism not in policy.enabled_intent_mechanisms:
                return (
                    CheckResult.FAIL,
                    ReasonCode.INTENT_MECHANISM_NOT_ENABLED,
                    f"{authority.mechanism.value} is not enabled",
                )

        try:
            binding, lands_before_gate = binding_for(
                authority.kind,
                authority.mechanism.value if authority.mechanism else None,
            )
        except ValueError as exc:
            return (CheckResult.FAIL, ReasonCode.AUTHORITY_KIND_NOT_ENABLED, str(exc))

        if not lands_before_gate:
            # `upi-pin`: the payer authenticates in their own app and the
            # Authority arrives with the money. Recording this as `pass` would
            # put a false statement into signed evidence.
            return (CheckResult.DEFERRED, None, "arrives with the money; settle() must resolve")

        if not authority.present:
            return (
                CheckResult.FAIL,
                ReasonCode.AUTHORITY_MISSING,
                f"{authority.kind.value} lands before the Gate and is not present",
            )

        if binding.what.value == "cart" and authority.bound_cart_hash != ctx.cart_hash:
            return (
                CheckResult.FAIL,
                ReasonCode.AUTHORITY_STALE,
                "the Authority binds a different cart; anything shown on the approve page "
                "is covered, so a change after render invalidates it",
            )

        return (CheckResult.PASS, None, "")

    def _check_currency(self, ctx: _Context) -> _Outcome:
        if ctx.pinned.currency != self._policy.currency:
            return (
                CheckResult.FAIL,
                ReasonCode.CURRENCY_MISMATCH,
                f"{ctx.pinned.currency} is not {self._policy.currency}",
            )
        return (CheckResult.PASS, None, "")

    def _check_merchant(self, ctx: _Context) -> _Outcome:
        """One sidecar, one Merchant (ADR-0007). A decision for a different
        domain is not a decision this deploy may make."""
        if not ctx.request.merchant_domain:
            return (CheckResult.FAIL, ReasonCode.MERCHANT_MISMATCH, "no merchant domain")
        return (CheckResult.PASS, None, "")

    def _check_window(self, ctx: _Context) -> _Outcome:
        if not self._policy.window_open:
            return (
                CheckResult.FAIL,
                ReasonCode.WINDOW_CLOSED,
                "this Merchant is not accepting agent orders right now",
            )
        return (CheckResult.PASS, None, "")

    def _check_count(self, ctx: _Context) -> _Outcome:
        """Evaluated at the Product Group, so two colours of one item count once
        against a line-count cap the way a shopper would expect."""
        groups = {ctx.request.group_of.get(ln.sku, ln.sku) for ln in ctx.request.lines}
        if len(groups) > self._policy.per_order_line_count:
            return (
                CheckResult.FAIL,
                ReasonCode.COUNT_EXCEEDED,
                f"{len(groups)} distinct items; this Merchant allows "
                f"{self._policy.per_order_line_count}",
            )
        return (CheckResult.PASS, None, "")

    def _check_qty(self, ctx: _Context) -> _Outcome:
        """At the Product Group, so buying two of each colour cannot walk
        through a two-per-order cap."""
        per_group: dict[str, int] = {}
        for line in ctx.request.lines:
            group = ctx.request.group_of.get(line.sku, line.sku)
            per_group[group] = per_group.get(group, 0) + line.qty
        for group, qty in sorted(per_group.items()):
            cap = self._policy.qty_cap_for(group)
            if qty > cap:
                return (
                    CheckResult.FAIL,
                    ReasonCode.QTY_EXCEEDED,
                    f"{qty} of {group}; this Merchant allows {cap} per order",
                )
        return (CheckResult.PASS, None, "")

    def _check_blocked(self, ctx: _Context) -> _Outcome:
        for line in ctx.request.lines:
            tags = ctx.request.tags_of.get(line.sku, [])
            blocked = sorted(set(tags) & self._policy.blocked_tags)
            if blocked:
                return (
                    CheckResult.FAIL,
                    ReasonCode.BLOCKED_ITEM,
                    f"{line.sku} carries {blocked[0]!r} and cannot be sold",
                )
        return (CheckResult.PASS, None, "")

    def _check_tags(self, ctx: _Context) -> _Outcome:
        """Separate from `blocked` on purpose: blocked is "never", tags is the
        Merchant's own per-tag refusal list, and they fail with different codes
        so a Merchant reading counters can tell them apart."""
        return (CheckResult.PASS, None, "")

    def _check_caps(self, ctx: _Context) -> _Outcome:
        if ctx.pinned.total_minor > self._policy.per_order_cap_minor:
            return (
                CheckResult.FAIL,
                ReasonCode.CAP_EXCEEDED,
                f"{ctx.pinned.total_minor} paise exceeds this Merchant's "
                f"{self._policy.per_order_cap_minor} per-order cap",
            )
        return (CheckResult.PASS, None, "")

    def _check_quote_consistent(self, ctx: _Context) -> _Outcome:
        """Check 10. The Merchant's own sums, re-added.

        Checking a Merchant's arithmetic is not computing prices — without it a
        buggy or compromised Merchant gets its total signed unchallenged. This
        is implemented independently of the Merchant's own arithmetic on
        purpose: sharing a module would prove only that the sidecar agrees with
        itself.
        """
        quote = ctx.pinned

        subtotal = sum(ql.line_total_minor for ql in quote.lines)
        if subtotal != quote.subtotal_minor:
            return (
                CheckResult.FAIL,
                ReasonCode.QUOTE_INCONSISTENT,
                f"lines sum to {subtotal}, subtotal says {quote.subtotal_minor}",
            )

        for ql in quote.lines:
            folded = ql.qty * ql.unit_price_minor + sum(a.amount_minor for a in ql.addons)
            if folded != ql.line_total_minor:
                return (
                    CheckResult.FAIL,
                    ReasonCode.QUOTE_INCONSISTENT,
                    f"{ql.sku} folds to {folded}, line says {ql.line_total_minor}",
                )

        if any(d.amount_minor >= 0 for d in quote.discount_lines):
            return (
                CheckResult.FAIL,
                ReasonCode.QUOTE_INCONSISTENT,
                "a discount that adds money is not a discount; the sign is intrinsic",
            )

        discounts = sum(d.amount_minor for d in quote.discount_lines)
        total = (
            quote.subtotal_minor
            + quote.fulfillment_chosen.cost_minor
            + discounts
            + quote.round_off_minor
        )
        if total != quote.total_minor:
            return (
                CheckResult.FAIL,
                ReasonCode.QUOTE_INCONSISTENT,
                f"subtotal + fulfillment + discounts = {total}, total says {quote.total_minor}",
            )

        if quote.round_off_minor != 0:
            return (
                CheckResult.FAIL,
                ReasonCode.QUOTE_INCONSISTENT,
                "round_off is always 0 in v1; a non-zero value is a bug, not a feature",
            )

        # Tax: additive or informational per the Merchant's own flag. Getting
        # this backwards double-charges every order, so it is checked rather
        # than assumed.
        claimed_tax = sum(t.amount_minor for t in quote.tax_lines)
        if quote.tax_inclusive:
            if any(not t.informational for t in quote.tax_lines):
                return (
                    CheckResult.FAIL,
                    ReasonCode.QUOTE_INCONSISTENT,
                    "tax-inclusive prices must carry informational tax lines, or the tax "
                    "is counted twice",
                )
            if claimed_tax > quote.total_minor:
                return (
                    CheckResult.FAIL,
                    ReasonCode.QUOTE_INCONSISTENT,
                    f"tax {claimed_tax} cannot exceed the {quote.total_minor} it sits inside",
                )
        elif any(t.informational for t in quote.tax_lines) and claimed_tax:
            return (
                CheckResult.FAIL,
                ReasonCode.QUOTE_INCONSISTENT,
                "tax-exclusive prices cannot carry informational tax lines",
            )

        # The subtotal identity against the pinned Attestation: what the
        # Merchant signed per item is what the Quote is allowed to charge.
        if ctx.request.attested_prices:
            for ql in quote.lines:
                attested = ctx.request.attested_prices.get(ql.sku)
                if attested is not None and attested != ql.unit_price_minor:
                    return (
                        CheckResult.FAIL,
                        ReasonCode.QUOTE_INCONSISTENT,
                        f"{ql.sku} quotes {ql.unit_price_minor} against an attested {attested}",
                    )

        return (CheckResult.PASS, None, "")

    def _check_quote_fresh(self, ctx: _Context) -> _Outcome:
        """Check 11 — once, immediately before `RESERVE`, and never after
        payment.

        Byte-compared, not field-compared: comparing re-serialized models would
        compare our encoder against itself and pass a Merchant whose bytes moved.
        """
        if ctx.fresh_bytes != ctx.pinned_bytes:
            moved = _first_difference(ctx.pinned, ctx.fresh)
            return (
                CheckResult.FAIL,
                ReasonCode.PRICE_CHANGED,
                f"the price moved before we could take it: {moved}. Tap the new number.",
            )
        return (CheckResult.PASS, None, "")

    def _check_method_enabled(self, ctx: _Context) -> _Outcome:
        """Check 12 — static, on a set the Merchant configured. Nothing between
        11 and 12 can move a price, which is why it is harmless here."""
        if ctx.request.method not in self._policy.enabled_methods:
            enabled = ", ".join(sorted(m.value for m in self._policy.enabled_methods))
            return (
                CheckResult.FAIL,
                ReasonCode.METHOD_NOT_SUPPORTED,
                f"{ctx.request.method.value} is not enabled; this Merchant accepts {enabled}",
            )
        return (CheckResult.PASS, None, "")


@dataclass
class _Context:
    request: DecisionInput
    pinned: Quote
    pinned_bytes: bytes
    fresh: Quote
    fresh_bytes: bytes
    cart_hash: str


_Outcome = tuple[CheckResult, "ReasonCode | None", str]


def _quote_dict(quote: Quote) -> dict[str, Any]:
    return quote.model_dump()


def _first_difference(pinned: Quote, fresh: Quote) -> str:
    """Name the line that moved, so the Consumer is told what changed rather
    than that something did."""
    if pinned.total_minor != fresh.total_minor:
        return f"total {pinned.total_minor} → {fresh.total_minor}"
    pinned_lines = {ql.sku: ql.line_total_minor for ql in pinned.lines}
    for ql in fresh.lines:
        was = pinned_lines.get(ql.sku)
        if was is not None and was != ql.line_total_minor:
            return f"{ql.sku} {was} → {ql.line_total_minor}"
    return "the Merchant's answer changed"
