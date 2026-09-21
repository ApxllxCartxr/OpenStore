"""What the tools actually do.

`/agent/mcp` answered every tool `{"accepted": true}` and ran nothing: `search`
returned no results from a catalogue door 1 serves twelve groups from, and
`add-line` added to nothing. This is the dispatch behind the tool set.

Two rules hold for every tool here:

- **No exact stock ever reaches an agent.** Door 2 returns integers because the
  Gate needs them; everything agent-facing goes through `trait.buckets` first.
  The single exception SPEC §16.9 allows is a refusal on a waited-on quantity,
  which names the number the Consumer can actually have.
- **No arithmetic.** Every total comes from door 9. A tool that could add up a
  basket would eventually disagree with the shop.
"""

from __future__ import annotations

import secrets
from typing import Any

from pydantic import ValidationError

from openstore.sidecar.basket import Basket
from openstore.sidecar.core.codes import (
    AvailabilityBucket,
    PaymentMethod,
    ReasonCode,
    ToolName,
)
from openstore.sidecar.trait.buckets import bucket_for, bucket_for_group, quantity_refusal_detail
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.errors import TraitError
from openstore.sidecar.trait.models import Destination


class ToolRefused(Exception):
    def __init__(self, code: ReasonCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail


async def _buckets(trait: TraitClient, skus: list[str], low: dict[str, int]) -> dict[str, str]:
    """Counts in, buckets out — the conversion that keeps door 2 private."""
    stock = await trait.stock_read(skus)
    return {sku: bucket_for(count, low.get(sku, 3)).value for sku, count in stock.items()}


async def search(trait: TraitClient, query: str) -> dict[str, Any]:
    """Match on any word, across the group and the variants under it.

    A whole-phrase match against the group name alone finds nothing for "black
    tote", because the group is "Tote" and `black` is a value on one of its
    option axes — so the shop looked empty for the most obvious search a
    Consumer could type.
    """
    catalogue = await trait.catalog_read()
    words = [w for w in query.strip().lower().split() if w]

    def haystack(group_id: str, name: str, description: str) -> str:
        variants = [i for i in catalogue.items if i.group_id == group_id]
        return " ".join(
            [name, description or ""]
            + [i.name for i in variants]
            + [i.sku for i in variants]
            + [v for i in variants for v in i.options.values()]
            + [t for i in variants for t in i.tags]
        ).lower()

    groups = [
        g
        for g in catalogue.groups
        if not words or any(w in haystack(g.id, g.name, g.description or "") for w in words)
    ]
    low = {i.sku: i.low_stock_threshold for i in catalogue.items}
    buckets = await _buckets(trait, [i.sku for i in catalogue.items], low)

    results = []
    for group in groups:
        items = [i for i in catalogue.items if i.group_id == group.id]
        if not items:
            continue
        results.append(
            {
                "group": group.id,
                "name": group.name,
                "from_minor": min(i.price_minor for i in items),
                "currency": "INR",
                "availability": bucket_for_group(
                    [AvailabilityBucket(buckets[i.sku]) for i in items]
                ).value,
                "variants": len(items),
                # The axes, not the variants: a group with one size needs no
                # question, and one with two can be asked about straight from
                # the search result instead of costing a `read-item` round trip
                # to learn that a question exists at all.
                "option_axes": group.option_axes,
                "image": group.media[0] if group.media else None,
            }
        )
    # Ordered, always: an agent that re-reads a shop must see the same order.
    results.sort(key=lambda r: str(r["name"]))
    return {"results": results, "query": query}


async def read_item(trait: TraitClient, group_id: str) -> dict[str, Any]:
    catalogue = await trait.catalog_read()
    group = next((g for g in catalogue.groups if g.id == group_id or g.slug == group_id), None)
    if group is None:
        raise ToolRefused(ReasonCode.NOT_FOUND, f"{group_id!r} is not a product group here.")
    items = [i for i in catalogue.items if i.group_id == group.id]
    low = {i.sku: i.low_stock_threshold for i in items}
    buckets = await _buckets(trait, [i.sku for i in items], low)
    return {
        "group": group.id,
        "name": group.name,
        "description": group.description,
        "option_axes": group.option_axes,
        "images": group.media,
        "variants": [
            {
                "sku": i.sku,
                "name": i.name,
                "options": i.options,
                "price_minor": i.price_minor,
                "currency": "INR",
                "availability": buckets.get(i.sku, "sold-out"),
                "images": i.media,
                "tags": i.tags,
            }
            for i in items
        ],
    }


async def add_line(
    trait: TraitClient, basket: Basket, sku: str, qty: int, parent: str | None
) -> dict[str, Any]:
    if qty < 1:
        raise ToolRefused(ReasonCode.NOT_FOUND, "A line needs a quantity of at least one.")
    catalogue = await trait.catalog_read()
    item = next((i for i in catalogue.items if i.sku == sku), None)
    if item is None:
        group = next((g for g in catalogue.groups if g.id == sku or g.slug == sku), None)
        if group is not None:
            raise ToolRefused(
                ReasonCode.VARIANT_REQUIRED,
                f"{sku!r} is a product group, not something that can be bought. Choose one "
                f"of its variants: {', '.join(i.sku for i in catalogue.items if i.group_id == group.id)}",
            )
        raise ToolRefused(ReasonCode.NOT_FOUND, f"{sku!r} is not a SKU this shop sells.")

    # Availability is checked here so the agent hears "two left" now rather than
    # at the tap, but the hold still happens only at the tap.
    stock = await trait.stock_read([sku])
    available = stock.get(sku, 0)
    wanted = qty + sum(ln.qty for ln in basket.lines if ln.sku == sku)
    if available < wanted:
        raise ToolRefused(ReasonCode.SOLD_OUT, quantity_refusal_detail(available, wanted))

    basket.add(sku, qty, parent)
    return await summary(trait, basket)


async def summary(trait: TraitClient, basket: Basket) -> dict[str, Any]:
    """The basket as the Consumer would see it — priced by the Merchant, or not
    priced at all."""
    body: dict[str, Any] = {
        "cart_id": basket.cart_id,
        "lines": [
            {"sku": ln.sku, "qty": ln.qty, **({"parent": ln.parent} if ln.parent else {})}
            for ln in basket.lines
        ],
        "destination_set": basket.destination is not None,
        "contact_set": bool(basket.contact),
        "fulfillment_option_id": basket.fulfillment_option_id or None,
        "discount_code": basket.discount_code,
    }
    if basket.quotable():
        assert basket.destination is not None
        quote, _ = await trait.quote(
            basket.lines,
            basket.destination,
            fulfillment_option_id=basket.fulfillment_option_id,
            discount_code=basket.discount_code,
        )
        body["quote"] = quote.model_dump(mode="json")
        body["total_minor"] = quote.total_minor
        body["currency"] = quote.currency
    else:
        body["quote"] = None
        body["needs"] = [
            name
            for name, missing in (
                ("lines", not basket.lines),
                ("destination", basket.destination is None),
                ("fulfillment", not basket.fulfillment_option_id),
            )
            if missing
        ]
    return body


async def fulfillment_options(trait: TraitClient, basket: Basket) -> dict[str, Any]:
    if basket.destination is None or not basket.lines:
        raise ToolRefused(
            ReasonCode.NOT_FOUND,
            "The shop quotes delivery against a Destination and a basket; set both first.",
        )
    quote, _ = await trait.quote(basket.lines, basket.destination)
    return {
        "options": [
            {
                "id": o.id,
                "label": o.label,
                "cost_minor": o.cost_minor,
                "eta_days": o.eta_days,
            }
            for o in quote.fulfillment_options
        ]
    }


#: What the Consumer must fix for each Destination field, in the order the
#: fields are asked for. A pydantic dump names patterns and doc URLs; the agent
#: — and the shopper behind it — needs the fix, not the schema.
_DESTINATION_FIXES: tuple[tuple[str, str], ...] = (
    ("line1", "a street address in line1"),
    ("city", "a city"),
    ("state", "a 2-letter state code (e.g. TN for Tamil Nadu)"),
    ("postal_code", "a 6-digit postal code"),
)


def _destination_fix(exc: ValidationError) -> str:
    bad = [
        fix
        for field, fix in _DESTINATION_FIXES
        if any(err["loc"][:1] == (field,) for err in exc.errors())
    ]
    if not bad:
        return (
            "That address needs a street address, a city, a 2-letter state code "
            "and a 6-digit postal code."
        )
    if len(bad) == 1:
        needs = bad[0]
    elif len(bad) == 2:
        needs = f"{bad[0]} and {bad[1]}"
    else:
        needs = f"{', '.join(bad[:-1])} and {bad[-1]}"
    return f"That address needs {needs}."


def set_destination(basket: Basket, payload: dict[str, Any]) -> None:
    try:
        basket.destination = Destination.model_validate(payload)
    except ValidationError as exc:
        raise ToolRefused(ReasonCode.NOT_FOUND, _destination_fix(exc)) from None
    except Exception:  # noqa: BLE001 — anything else is still a shape problem
        raise ToolRefused(
            ReasonCode.NOT_FOUND,
            "That address needs a street address, a city, a 2-letter state code "
            "and a 6-digit postal code.",
        ) from None
    # A Destination change re-prices and re-hashes, so a chosen option no longer
    # applies to it.
    basket.fulfillment_option_id = ""


def set_contact(basket: Basket, payload: dict[str, Any]) -> None:
    contact = {k: str(v) for k, v in payload.items() if k in {"email", "phone"} and v}
    if not contact:
        raise ToolRefused(
            ReasonCode.NOT_FOUND,
            "A Contact Point is an email, a phone, or both — and one is needed.",
        )
    basket.contact = contact


def clear_basket(basket: Basket) -> dict[str, Any]:
    """Empty the basket back to a fresh state: lines, Destination, Contact
    Point, chosen fulfillment and code all go, and the cart id rotates so a
    later checkout cannot idempotently return an order from the cleared cart.

    In place, so the one save path in `_run_tool` persists exactly this: a
    delete-then-save here would race the save that follows dispatch and
    resurrect the cleared basket.

    Used when the shopper starts over. A basket that outlives its conversation
    puts old lines into the next quote, and every one of those lines breaks
    the invariant that a cart line traces to something this conversation asked
    for or accepted.
    """
    removed = [ln.sku for ln in basket.lines]
    basket.lines = []
    basket.destination = None
    basket.contact = {}
    basket.fulfillment_option_id = ""
    basket.discount_code = None
    basket.cart_id = f"cart_{secrets.token_hex(8)}"
    return {
        "cleared": True,
        "removed": removed,
        "quote": None,
        "needs": ["lines", "destination", "fulfillment"],
    }


async def choose_fulfillment(trait: TraitClient, basket: Basket, option_id: str) -> dict[str, Any]:
    options = await fulfillment_options(trait, basket)
    if option_id not in {o["id"] for o in options["options"]}:
        raise ToolRefused(
            ReasonCode.NOT_FOUND,
            f"{option_id!r} is not offered for that Destination. Offered: "
            f"{', '.join(str(o['id']) for o in options['options'])}",
        )
    basket.fulfillment_option_id = option_id
    return await summary(trait, basket)


async def apply_public_code(trait: TraitClient, basket: Basket, code: str) -> dict[str, Any]:
    """Public codes only. A private code is entered by the Consumer on the
    approve page and never travels through an agent (ADR-0015)."""
    if not basket.quotable():
        raise ToolRefused(
            ReasonCode.NOT_FOUND,
            "A code re-prices a basket, so the basket has to be quotable first.",
        )
    assert basket.destination is not None
    previous = basket.discount_code
    basket.discount_code = code
    try:
        return await summary(trait, basket)
    except TraitError as exc:
        basket.discount_code = previous
        raise ToolRefused(exc.code, exc.detail) from None


def unsupported(tool: ToolName) -> ToolRefused:
    return ToolRefused(
        ReasonCode.NOT_FOUND,
        f"{tool.value} is listed but not wired in this build. It refuses rather than "
        f"pretending to have worked.",
    )


def method_for(name: str) -> PaymentMethod:
    try:
        return PaymentMethod(name)
    except ValueError:
        return PaymentMethod.UPI
