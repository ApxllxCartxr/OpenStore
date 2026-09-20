"""The exposure boundary: exact counts go in, Availability Buckets come out.

This lives here, at the boundary, and **not at the door** (A2). Door 2 returns
integers because the Gate needs them; the rule that an agent never sees one is
enforced at the single place every agent-facing response passes through. Put it
at the door and every future caller has to remember to bucket — and one that
forgets hands a self-registered stranger an inventory-probing oracle.

Why it matters, stated once: an exact count handed to anyone who asks is both
competitive intelligence and an oracle. Ask for 50, get refused, ask for 25,
get refused, binary-search the Merchant's stock in eight requests.
"""

from __future__ import annotations

from collections.abc import Iterable

from openstore.sidecar.core.codes import AvailabilityBucket


def bucket_for(available: int, low_stock_threshold: int) -> AvailabilityBucket:
    """One Catalogue Item's bucket, cut at that item's own threshold.

    `available` is an int >= 0, always — never null, never a float. A missing or
    negative count fails catalogue load loud rather than arriving here (SPEC §5).
    """
    if available < 0:
        raise ValueError(f"stock is int >= 0, never {available}; this should have failed at load")
    if available == 0:
        return AvailabilityBucket.SOLD_OUT
    if available <= low_stock_threshold:
        return AvailabilityBucket.LOW_STOCK
    return AvailabilityBucket.IN_STOCK


def bucket_for_group(item_buckets: Iterable[AvailabilityBucket]) -> AvailabilityBucket:
    """A Product Group reads in-stock when **any** of its items is.

    A group is presentation, so its bucket is a summary for a page, never a
    thing to reserve against. Red in stock and black sold out is an in-stock
    group whose black variant refuses at the door — which is correct, and is why
    caps and quantities are evaluated at the group while stock is not.
    """
    buckets = list(item_buckets)
    if not buckets:
        return AvailabilityBucket.SOLD_OUT
    if AvailabilityBucket.IN_STOCK in buckets:
        return AvailabilityBucket.IN_STOCK
    if AvailabilityBucket.LOW_STOCK in buckets:
        return AvailabilityBucket.LOW_STOCK
    return AvailabilityBucket.SOLD_OUT


def quantity_refusal_detail(available: int, requested: int) -> str:
    """The one deliberate exception, taken with eyes open against the smoothness
    law (SPEC §5, §11).

    A quantity refusal the Consumer is *actively waiting on* names the exact
    remaining count, because "try fewer" without a number is not a fix — it is a
    guessing game with a human in it. This is an oracle and is treated as one:
    the caller must rate-limit it per agent (§16.8: 20/hour) and count it.

    It is a function rather than an inline f-string so there is exactly one place
    in the system that puts a count in front of an agent, and it is greppable.
    """
    if requested <= available:
        raise ValueError(
            f"not a refusal: {requested} <= {available}. This path exists only to explain "
            f"a refusal, and calling it otherwise leaks a count for no reason."
        )
    if available == 0:
        return "Sold out."
    return f"Only {available} left; try {available} or fewer."
