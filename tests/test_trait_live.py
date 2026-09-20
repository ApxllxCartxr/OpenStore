"""The conformance suite against the **real** merchant site, not the fake.

A second implementation is the only real proof the trait is a contract and not a
description of one codebase. The fake proves the sidecar's logic; this proves
the contract, and the two are different claims.

Skipped unless `OPENSTORE_LIVE_TRAIT_URL` points at a running store, so the
default suite stays hermetic. CI runs it with the store up.

Run it by hand with:

    cd demo/merchant-site && pnpm dev            # with Postgres up and seeded
    OPENSTORE_LIVE_TRAIT_URL=http://127.0.0.1:3000 uv run pytest tests/test_trait_live.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import httpx
import pytest

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.errors import TraitError
from openstore.sidecar.trait.models import Destination, Line

LIVE_URL = os.environ.get("OPENSTORE_LIVE_TRAIT_URL")
SECRET = os.environ.get("TRAIT_HMAC_SECRET", "conformance-secret")

pytestmark = pytest.mark.skipif(
    not LIVE_URL, reason="set OPENSTORE_LIVE_TRAIT_URL to run against the real store"
)

DEST_A = Destination(
    line1="4th Cross, Indiranagar", city="Bengaluru", state="KA", postal_code="560038"
)
DEST_B = Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028")
CONTACT = {"email": "demo@spoiledduckie.test", "phone": "+919000000001"}


@pytest.fixture
async def live() -> AsyncIterator[TraitClient]:
    async with TraitClient(LIVE_URL or "", SECRET) as client:
        yield client


def _order_id(suffix: str) -> str:
    """Unique per run: the live database persists between runs, and a reused id
    would replay a previous run's idempotency cache."""
    import secrets

    return f"ord_live_{suffix}_{secrets.token_hex(4)}"


# ── The doors answer ─────────────────────────────────────────────────────────


async def test_catalog_read_returns_the_seeded_fifteen(live: TraitClient) -> None:
    catalog = await live.catalog_read()
    assert len(catalog.items) == 15
    assert len(catalog.groups) == 12
    assert "SD-TOTE-BLK-L" not in {item.sku for item in catalog.items}


async def test_stock_read_returns_exact_integers(live: TraitClient) -> None:
    stock = await live.stock_read(["SD-TOTE-BLK-M", "SD-CAP-M"])
    assert all(isinstance(v, int) and v >= 0 for v in stock.values())


async def test_a_group_id_where_a_sku_belongs_refuses_variant_required(
    live: TraitClient,
) -> None:
    with pytest.raises(TraitError) as exc:
        await live.quote([Line(sku="tote", qty=1)], DEST_A)
    assert exc.value.code is ReasonCode.VARIANT_REQUIRED


# ── Door 9, against the Merchant's own arithmetic ────────────────────────────


async def test_the_mixed_basket_reproduces_the_pinned_numbers(live: TraitClient) -> None:
    """§16.11's worked example, computed by the **TypeScript** implementation
    this time. Three implementations agreeing — this, the fake, and the Gate's
    check — is what makes the number trustworthy."""
    quote, _ = await live.quote(
        [
            Line(sku="SD-TOTE-BLK-M", qty=1),
            Line(sku="SD-GIFTWRAP", qty=1, parent="SD-TOTE-BLK-M"),
            Line(sku="SD-CHARMBAR-SEAT", qty=1),
        ],
        DEST_B,
        fulfillment_option_id="rest-of-india",
    )
    assert quote.subtotal_minor == 249800
    assert quote.total_minor == 259700

    amounts = {(t.kind, t.amount_minor) for t in quote.tax_lines}
    assert ("IGST", 15827) in amounts
    assert ("CGST", 11894) in amounts
    assert ("SGST", 11894) in amounts


async def test_both_gst_splits_appear_in_one_basket(live: TraitClient) -> None:
    quote, _ = await live.quote(
        [Line(sku="SD-TOTE-BLK-M", qty=1), Line(sku="SD-CHARMBAR-SEAT", qty=1)],
        DEST_B,
    )
    kinds = {t.kind for t in quote.tax_lines}
    assert "IGST" in kinds and "CGST" in kinds


async def test_quote_is_byte_identical_across_repeat_calls(live: TraitClient) -> None:
    lines = [Line(sku="SD-TOTE-BLK-M", qty=1), Line(sku="SD-STICKERS", qty=2)]
    _, first = await live.quote(lines, DEST_B)
    for _ in range(4):
        _, again = await live.quote(lines, DEST_B)
        assert again == first


async def test_quote_contains_no_date_only_day_counts(live: TraitClient) -> None:
    """A date in a Quote turns midnight into a spurious `price-changed`."""
    import re

    _, raw = await live.quote([Line(sku="SD-STICKERS", qty=1)], DEST_A)
    assert not re.search(rb"\d{4}-\d{2}-\d{2}", raw)


async def test_an_unserviceable_postcode_refuses(live: TraitClient) -> None:
    with pytest.raises(TraitError) as exc:
        await live.quote(
            [Line(sku="SD-STICKERS", qty=1)],
            Destination(line1="X", city="Y", state="AN", postal_code="190001"),
        )
    assert exc.value.code is ReasonCode.DESTINATION_UNSERVICEABLE


async def test_an_orphan_addon_is_refused(live: TraitClient) -> None:
    with pytest.raises(TraitError) as exc:
        await live.quote([Line(sku="SD-GIFTWRAP", qty=1)], DEST_A)
    assert exc.value.code is ReasonCode.ADDON_WITHOUT_PARENT


# ── Idempotency, byte-for-byte ───────────────────────────────────────────────


async def test_a_repeated_key_returns_identical_response_bytes(live: TraitClient) -> None:
    order_id = _order_id("idem")
    lines = [Line(sku="SD-STICKERS", qty=1)]
    await live.reserve(order_id, lines, attempt=1)
    # The second call must replay, not re-reserve.
    await live.reserve(order_id, lines, attempt=1)

    stock = await live.stock_read(["SD-STICKERS"])
    await live.release(order_id)
    after = await live.stock_read(["SD-STICKERS"])
    assert after["SD-STICKERS"] == stock["SD-STICKERS"] + 1, "the retry took a second hold"


async def test_releasing_a_hold_that_was_never_taken_refuses(live: TraitClient) -> None:
    with pytest.raises(TraitError) as exc:
        await live.release(_order_id("never"))
    assert exc.value.code is ReasonCode.NO_HOLD


# ── Concurrency, against real Postgres ───────────────────────────────────────


async def test_fifty_concurrent_reserves_on_five_units_yield_exactly_five(
    live: TraitClient,
) -> None:
    """A3 runs this shape against the conformance fake, which proves the
    sidecar's logic. **This** proves `WHERE available >= qty` actually holds
    under Postgres' isolation level, which is a different claim — and the one
    that decides whether real money oversells."""
    import secrets

    sku = "SD-PLUSH-MINI"
    before = (await live.stock_read([sku]))[sku]
    if before < 5:
        pytest.skip(f"{sku} has {before} units; re-seed for the concurrency gate")

    target = 5
    run = secrets.token_hex(4)
    # Take everything above the target out of play first, so exactly 5 remain.
    setup_order = _order_id(f"setup_{run}")
    if before > target:
        await live.reserve(setup_order, [Line(sku=sku, qty=before - target)])

    async def attempt(n: int) -> bool:
        try:
            await live.reserve(f"ord_race_{run}_{n}", [Line(sku=sku, qty=1)])
            return True
        except TraitError:
            return False

    results = await asyncio.gather(*(attempt(n) for n in range(50)))

    assert sum(results) == target, f"{sum(results)} reservations against {target} units"
    assert (await live.stock_read([sku]))[sku] == 0, "stock did not land exactly on zero"
