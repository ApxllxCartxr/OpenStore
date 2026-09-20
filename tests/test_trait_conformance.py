"""The conformance suite — A2's DONE WHEN.

Everything here runs through the real client against the fake Merchant over
real HTTP, so HMAC verification, idempotency replay and status mapping are
exercised rather than stepped around.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from openstore.sidecar.core.codes import AvailabilityBucket, Door, ReasonCode
from openstore.sidecar.trait import buckets
from openstore.sidecar.trait.client import TraitClient
from openstore.sidecar.trait.errors import TraitError, TraitProtocolError
from openstore.sidecar.trait.fake import FakeMerchant, apportion, extract_tax, make_app
from openstore.sidecar.trait.models import Destination, Line
from openstore.sidecar.trait.seed import SEED_ITEMS, flat_price, seeded

SECRET = "conformance-secret"  # noqa: S105 - a test secret, never a deployment one

DEST_A = Destination(
    line1="4th Cross, Indiranagar", city="Bengaluru", state="KA", postal_code="560038"
)
DEST_B = Destination(line1="Dadar West", city="Mumbai", state="MH", postal_code="400028")
CONTACT = {"email": "demo@spoiledduckie.test", "phone": "+919000000001"}


def _client_for(merchant: FakeMerchant, secret: str = SECRET) -> TraitClient:
    """The client reaches the fake over real HTTP through an ASGI transport, so
    signatures, idempotency replay and status mapping are all exercised."""
    transport = httpx.ASGITransport(app=make_app(merchant, SECRET))
    return TraitClient(
        "http://merchant.internal",
        secret,
        client=httpx.AsyncClient(transport=transport, base_url="http://merchant.internal"),
    )


@pytest.fixture
def merchant() -> FakeMerchant:
    return seeded()


@pytest.fixture
async def client(merchant: FakeMerchant) -> AsyncIterator[TraitClient]:
    async with _client_for(merchant) as c:
        yield c


# ── The seed is the seed ─────────────────────────────────────────────────────


def test_fifteen_items_and_the_absent_combination_is_absent(merchant: FakeMerchant) -> None:
    """§16.3's table has 16 rows and 15 are seeded. `SD-TOTE-BLK-L` must not
    exist at all — not zero-stocked, not archived, not null-priced."""
    assert len(SEED_ITEMS) == 15
    assert len(merchant.items) == 15
    assert "SD-TOTE-BLK-L" not in merchant.items
    assert "SD-TOTE-BLK-L" not in merchant.stock


def test_no_null_or_negative_stock(merchant: FakeMerchant) -> None:
    assert all(isinstance(v, int) and v >= 0 for v in merchant.stock.values())


# ── Doors answer ─────────────────────────────────────────────────────────────


async def test_catalog_read(client: TraitClient) -> None:
    catalog = await client.catalog_read()
    assert len(catalog.items) == 15
    assert {g.id for g in catalog.groups} >= {"tote", "cap", "charmbar"}


async def test_stock_read_returns_exact_integers(client: TraitClient) -> None:
    """Door 2 is the private network. The count is the point."""
    assert await client.stock_read(["SD-TOTE-BLK-M", "SD-CAP-M"]) == {
        "SD-TOTE-BLK-M": 12,
        "SD-CAP-M": 2,
    }


# ── variant-required at every door taking a SKU ──────────────────────────────


async def _stock_read_group(c: TraitClient) -> object:
    return await c.stock_read(["tote"])


async def _reserve_group(c: TraitClient) -> object:
    return await c.reserve("ord_1", [Line(sku="tote", qty=1)])


async def _restock_group(c: TraitClient) -> object:
    return await c.restock("ord_1", [Line(sku="tote", qty=1)])


async def _quote_group(c: TraitClient) -> object:
    return await c.quote([Line(sku="tote", qty=1)], DEST_A)


async def _create_group(c: TraitClient) -> object:
    return await c.orders_create("cart_1", [Line(sku="tote", qty=1)], DEST_A, CONTACT, "karnataka")


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(_stock_read_group, id="stock.read"),
        pytest.param(_reserve_group, id="reserve"),
        pytest.param(_restock_group, id="restock"),
        pytest.param(_quote_group, id="quote"),
        pytest.param(_create_group, id="orders.create"),
    ],
)
async def test_group_id_where_a_sku_belongs_refuses_variant_required(
    client: TraitClient, call: Callable[[TraitClient], Awaitable[object]]
) -> None:
    """A group id anywhere a SKU belongs refuses rather than having a size
    guessed for it."""
    with pytest.raises(TraitError) as exc:
        await call(client)
    assert exc.value.code is ReasonCode.VARIANT_REQUIRED
    assert exc.value.status_code == 400


# ── Retry-safety: same key, same result ──────────────────────────────────────


async def test_retry_with_the_same_key_does_not_hold_twice(
    client: TraitClient, merchant: FakeMerchant
) -> None:
    lines = [Line(sku="SD-TOTE-BLK-M", qty=2)]
    await client.reserve("ord_retry", lines, attempt=1)
    assert merchant.stock["SD-TOTE-BLK-M"] == 10

    await client.reserve("ord_retry", lines, attempt=1)  # the crash-and-retry case
    assert merchant.stock["SD-TOTE-BLK-M"] == 10, "a retry took a second hold"


async def test_a_retried_refusal_refuses_identically(
    client: TraitClient, merchant: FakeMerchant
) -> None:
    """Same key, same result — including a refusal. A retry that succeeds where
    the first attempt refused is a second hold with extra steps."""
    with pytest.raises(TraitError) as first:
        await client.reserve("ord_x", [Line(sku="SD-CAP-M", qty=5)], attempt=1)
    merchant.stock["SD-CAP-M"] = 50  # the shop restocked in between
    with pytest.raises(TraitError) as second:
        await client.reserve("ord_x", [Line(sku="SD-CAP-M", qty=5)], attempt=1)
    assert first.value.code is second.value.code is ReasonCode.SOLD_OUT


async def test_a_new_attempt_number_is_a_new_request(
    client: TraitClient, merchant: FakeMerchant
) -> None:
    await client.reserve("ord_a", [Line(sku="SD-TOTE-BLK-M", qty=1)], attempt=1)
    await client.reserve("ord_a", [Line(sku="SD-TOTE-BLK-M", qty=1)], attempt=2)
    assert merchant.stock["SD-TOTE-BLK-M"] == 10


async def test_a_mutating_door_without_a_key_is_refused_before_it_is_sent() -> None:
    """Caught client-side: a retry without a key is how a hold is taken twice,
    and the check belongs where it cannot be forgotten."""
    async with _client_for(seeded()) as c:
        with pytest.raises(TraitProtocolError, match="idempotency"):
            await c._call(Door.RESERVE, {"order_id": "x", "lines": []})  # noqa: SLF001


# ── Stock is negative-proof, and all-or-nothing ──────────────────────────────


async def test_reserve_is_negative_proof(client: TraitClient, merchant: FakeMerchant) -> None:
    with pytest.raises(TraitError) as exc:
        await client.reserve("ord_b", [Line(sku="SD-CAP-M", qty=3)])
    assert exc.value.code is ReasonCode.SOLD_OUT
    assert exc.value.status_code == 409
    assert merchant.stock["SD-CAP-M"] == 2, "a refused reserve moved stock"


async def test_a_sold_out_line_reserves_none_of_the_basket(
    client: TraitClient, merchant: FakeMerchant
) -> None:
    """Checked first, applied second. A half-reserved basket leaves the caller
    holding stock it has no order to release."""
    with pytest.raises(TraitError):
        await client.reserve(
            "ord_c",
            [Line(sku="SD-TOTE-BLK-M", qty=1), Line(sku="SD-CAP-M", qty=99)],
        )
    assert merchant.stock["SD-TOTE-BLK-M"] == 12


async def test_the_quantity_refusal_names_the_count(client: TraitClient) -> None:
    """The one deliberate exception to bucketing: a refusal the Consumer is
    waiting on names the number, because 'try fewer' without one is a guessing
    game with a human in it (SPEC §5)."""
    with pytest.raises(TraitError) as exc:
        await client.reserve("ord_d", [Line(sku="SD-CAP-M", qty=3)])
    assert "Only 2 left" in exc.value.detail


async def test_release_returns_the_stock(client: TraitClient, merchant: FakeMerchant) -> None:
    await client.reserve("ord_e", [Line(sku="SD-TOTE-BLK-M", qty=3)])
    assert merchant.stock["SD-TOTE-BLK-M"] == 9
    await client.release("ord_e")
    assert merchant.stock["SD-TOTE-BLK-M"] == 12


async def test_releasing_a_hold_that_was_never_taken_refuses(client: TraitClient) -> None:
    """A `sold-out` failure closes no hold because none was opened, and the
    trait must say so rather than accept a release that balances nothing."""
    with pytest.raises(TraitError) as exc:
        await client.release("ord_never")
    assert exc.value.code is ReasonCode.NO_HOLD


# ── External sales win automatically ─────────────────────────────────────────


async def test_an_external_sale_is_visible_to_the_next_read(
    client: TraitClient, merchant: FakeMerchant
) -> None:
    """A walk-in bought one. No event, no webhook — the next read simply sees a
    lower number, which is why the Gate re-reads fresh every time (SPEC §5)."""
    assert (await client.stock_read(["SD-TOTE-RED-L"]))["SD-TOTE-RED-L"] == 4
    merchant.external_sale("SD-TOTE-RED-L", 4)
    assert (await client.stock_read(["SD-TOTE-RED-L"]))["SD-TOTE-RED-L"] == 0

    with pytest.raises(TraitError) as exc:
        await client.reserve("ord_f", [Line(sku="SD-TOTE-RED-L", qty=1)])
    assert exc.value.code is ReasonCode.SOLD_OUT


# ── Door 9 determinism ───────────────────────────────────────────────────────


async def test_quote_is_byte_identical_across_repeat_calls(client: TraitClient) -> None:
    """Identical inputs against unchanged state return identical bytes, any
    number of times."""
    lines = [Line(sku="SD-TOTE-BLK-M", qty=1), Line(sku="SD-CHARMBAR-SEAT", qty=1)]
    _, first = await client.quote(lines, DEST_B)
    for _ in range(5):
        _, again = await client.quote(lines, DEST_B)
        assert again == first


async def test_quote_line_order_does_not_change_the_bytes(client: TraitClient) -> None:
    a = [Line(sku="SD-TOTE-BLK-M", qty=1), Line(sku="SD-STICKERS", qty=1)]
    b = [Line(sku="SD-STICKERS", qty=1), Line(sku="SD-TOTE-BLK-M", qty=1)]
    assert (await client.quote(a, DEST_A))[1] == (await client.quote(b, DEST_A))[1]


async def test_quote_carries_no_clock(client: TraitClient) -> None:
    """An ETA is a day count, never a date — a date turns midnight into a
    spurious `price-changed`."""
    quote, _ = await client.quote([Line(sku="SD-STICKERS", qty=1)], DEST_A)
    assert quote.fulfillment_options[0].eta_days == 2
    assert all(isinstance(o.eta_days, int) for o in quote.fulfillment_options)


async def test_a_drifting_merchant_produces_different_bytes(client: TraitClient) -> None:
    """The Gate's `quote-fresh` has something real to catch."""
    async with _client_for(seeded(quote_drift_paise=100)) as c:
        lines = [Line(sku="SD-STICKERS", qty=1)]
        _, first = await c.quote(lines, DEST_A)
        _, second = await c.quote(lines, DEST_A)
        assert first != second


# ── The pinned worked example (§16.11) ───────────────────────────────────────


async def test_the_mixed_basket_reproduces_the_pinned_numbers(client: TraitClient) -> None:
    """§16.11's table, computed by the Merchant rather than copied. One Quote
    carrying IGST on the tote and CGST/SGST on the charm-bar seat, because the
    seat is a service performed in Karnataka whatever the Destination."""
    quote, _ = await client.quote(
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
    assert quote.round_off_minor == 0

    by_sku = {ql.sku: ql for ql in quote.lines}
    assert by_sku["SD-TOTE-BLK-M"].line_total_minor == 99800  # gift-wrap folded in
    assert by_sku["SD-TOTE-BLK-M"].place_of_supply == "MH"
    assert by_sku["SD-CHARMBAR-SEAT"].place_of_supply == "KA"

    amounts = {(t.kind, t.amount_minor) for t in quote.tax_lines}
    assert ("IGST", 15827) in amounts
    assert ("CGST", 11894) in amounts
    assert ("SGST", 11894) in amounts


async def test_an_addon_folds_into_its_parent_and_is_never_a_quote_line(
    client: TraitClient,
) -> None:
    quote, _ = await client.quote(
        [
            Line(sku="SD-CHARMBAR-SEAT", qty=1),
            Line(sku="SD-GIFTWRAP", qty=1, parent="SD-CHARMBAR-SEAT"),
        ],
        DEST_A,
    )
    assert [ql.sku for ql in quote.lines] == ["SD-CHARMBAR-SEAT"]
    seat = quote.lines[0]
    assert seat.line_total_minor == 159900
    assert [a.sku for a in seat.addons] == ["SD-GIFTWRAP"]
    # Composite supply: gift-wrap on a service is taxed as that service.
    assert seat.gst_rate_bp == 1800
    assert seat.place_of_supply == "KA"


async def test_an_orphan_addon_is_refused(client: TraitClient) -> None:
    with pytest.raises(TraitError) as exc:
        await client.quote([Line(sku="SD-GIFTWRAP", qty=1)], DEST_A)
    assert exc.value.code is ReasonCode.ADDON_WITHOUT_PARENT
    assert exc.value.status_code == 400


async def test_an_addon_naming_a_line_that_is_not_there_is_refused(client: TraitClient) -> None:
    with pytest.raises(TraitError) as exc:
        await client.quote(
            [
                Line(sku="SD-STICKERS", qty=1),
                Line(sku="SD-GIFTWRAP", qty=1, parent="SD-TOTE-BLK-M"),
            ],
            DEST_A,
        )
    assert exc.value.code is ReasonCode.ADDON_WITHOUT_PARENT


async def test_odd_paise_goes_to_sgst(client: TraitClient) -> None:
    quote, _ = await client.quote([Line(sku="SD-STICKERS", qty=1)], DEST_A)
    cgst = next(t.amount_minor for t in quote.tax_lines if t.kind == "CGST")
    sgst = next(t.amount_minor for t in quote.tax_lines if t.kind == "SGST")
    assert (cgst, sgst) == (1891, 1892)
    assert sgst - cgst == 1


async def test_a_discount_apportions_by_largest_remainder(client: TraitClient) -> None:
    quote, _ = await client.quote(
        [Line(sku="SD-TOTE-BLK-M", qty=1), Line(sku="SD-PHONECHARM", qty=1)],
        DEST_A,
        discount_code="SPOILED10",
    )
    assert quote.subtotal_minor == 134800
    assert quote.discount_lines[0].amount_minor == -10000
    assert quote.total_minor == 129700
    amounts = {(t.kind, t.rate_bp, t.amount_minor) for t in quote.tax_lines}
    assert ("CGST", 900, 6597) in amounts
    assert ("SGST", 900, 6598) in amounts
    assert ("CGST", 150, 629) in amounts


def test_apportionment_is_exact_and_ties_break_by_sku() -> None:
    """A lost paise is a total that does not add up, which the Gate refuses."""
    shares = apportion(10000, [("SD-TOTE-BLK-M", 89900), ("SD-PHONECHARM", 44900)])
    assert shares == {"SD-TOTE-BLK-M": 6669, "SD-PHONECHARM": 3331}
    assert sum(shares.values()) == 10000

    even = apportion(1, [("b-sku", 50), ("a-sku", 50)])
    assert even == {"a-sku": 1, "b-sku": 0}, "an exact tie goes to the lower SKU"


def test_extract_tax_is_half_up_not_bankers() -> None:
    """`round()` would give 2 here. §16.11 requires ROUND_HALF_UP, and the two
    disagree on exactly the .5 cases money hits."""
    assert extract_tax(1150, 10000) == 575
    assert extract_tax(0, 1800) == 0
    assert extract_tax(100, 0) == 0


# ── The flat-price Merchant ──────────────────────────────────────────────────


async def test_flat_price_merchant_quotes_zero_value_lines_unchanged() -> None:
    """The lines exist and carry 0 rather than being omitted. A reader that has
    to handle 'the key is missing' as well as 'the value is zero' has two paths
    where it needs one."""
    async with _client_for(flat_price()) as c:
        quote, _ = await c.quote([Line(sku="SD-STICKERS", qty=2)], DEST_B)
        assert quote.subtotal_minor == 39800
        assert quote.total_minor == 39800
        assert quote.fulfillment_chosen.cost_minor == 0
        assert quote.fulfillment_options[0].cost_minor == 0
        assert all(t.amount_minor == 0 for t in quote.tax_lines)
        assert quote.tax_inclusive is False


async def test_flat_price_quote_is_still_byte_deterministic() -> None:
    async with _client_for(flat_price()) as c:
        lines = [Line(sku="SD-KEYCHAIN", qty=1)]
        assert (await c.quote(lines, DEST_A))[1] == (await c.quote(lines, DEST_A))[1]


# ── Destinations and codes ───────────────────────────────────────────────────


async def test_an_unserviceable_postcode_refuses(client: TraitClient) -> None:
    with pytest.raises(TraitError) as exc:
        await client.quote(
            [Line(sku="SD-STICKERS", qty=1)],
            Destination(line1="X", city="Y", state="AN", postal_code="190001"),
        )
    assert exc.value.code is ReasonCode.DESTINATION_UNSERVICEABLE


async def test_every_wrong_code_refuses_identically(client: TraitClient) -> None:
    """No message and no timing tell, or door 9 answers 'is this a code?' all
    day (SPEC §12)."""
    details = set()
    for attempt in ("NOPE", "SPOILED11", "DUCK-0000-0000"):
        with pytest.raises(TraitError) as exc:
            await client.quote([Line(sku="SD-STICKERS", qty=1)], DEST_A, discount_code=attempt)
        assert exc.value.code is ReasonCode.CODE_INVALID
        details.add(exc.value.detail)
    assert len(details) == 1, f"the refusals differ and that is an oracle: {details}"


async def test_a_single_use_code_cannot_be_spent_twice(client: TraitClient) -> None:
    """Held under the same idempotency key as the stock, so two carts racing
    cannot both spend it."""
    await client.reserve("ord_g", [Line(sku="SD-STICKERS", qty=1)], discount_code="DUCK-7F3K-9QWX")
    with pytest.raises(TraitError) as exc:
        await client.reserve(
            "ord_h", [Line(sku="SD-STICKERS", qty=1)], discount_code="DUCK-7F3K-9QWX"
        )
    assert exc.value.code is ReasonCode.CODE_INVALID


async def test_releasing_a_hold_returns_the_code_too(client: TraitClient) -> None:
    await client.reserve("ord_i", [Line(sku="SD-STICKERS", qty=1)], discount_code="DUCK-7F3K-9QWX")
    await client.release("ord_i")
    await client.reserve("ord_j", [Line(sku="SD-STICKERS", qty=1)], discount_code="DUCK-7F3K-9QWX")


# ── Orders ───────────────────────────────────────────────────────────────────


async def test_orders_create_returns_the_salt_and_orders_read_never_does(
    client: TraitClient,
) -> None:
    """§6.3a. Erasure deletes the row and the salt with it, and the evidence
    commitments become permanently unopenable — which only holds if the salt
    never leaks out of this one response."""
    created = await client.orders_create(
        "cart_k", [Line(sku="SD-STICKERS", qty=1)], DEST_A, CONTACT, "karnataka"
    )
    assert len(created.order_salt_hex) == 32

    order = await client.orders_read(created.order_id)
    assert "order_salt" not in order.model_dump()
    assert order.status.value == "pending"


async def test_orders_create_is_idempotent_on_cart_id(
    client: TraitClient, merchant: FakeMerchant
) -> None:
    """The crash-between-request-and-response case, which is exactly where
    duplicate orders are born."""
    first = await client.orders_create(
        "cart_dup", [Line(sku="SD-STICKERS", qty=1)], DEST_A, CONTACT, "karnataka"
    )
    second = await client.orders_create(
        "cart_dup", [Line(sku="SD-STICKERS", qty=1)], DEST_A, CONTACT, "karnataka"
    )
    assert first.order_id == second.order_id
    assert first.order_salt_hex == second.order_salt_hex
    assert len(merchant.orders) == 1


async def test_set_status_is_same_key_same_result(client: TraitClient) -> None:
    created = await client.orders_create(
        "cart_m", [Line(sku="SD-STICKERS", qty=1)], DEST_A, CONTACT, "karnataka"
    )
    a = await client.orders_set_status(created.order_id, "confirmed", "tapped")
    b = await client.orders_set_status(created.order_id, "confirmed", "tapped")
    assert a == b


async def test_reading_an_unknown_order_is_not_found(client: TraitClient) -> None:
    with pytest.raises(TraitError) as exc:
        await client.orders_read("ord_nope")
    assert exc.value.code is ReasonCode.NOT_FOUND
    assert exc.value.status_code == 404


# ── Signing ──────────────────────────────────────────────────────────────────


async def test_a_wrong_secret_is_refused(merchant: FakeMerchant) -> None:
    async with _client_for(merchant, secret="not-the-secret") as c:
        with pytest.raises(TraitError) as exc:
            await c.catalog_read()
        assert exc.value.code is ReasonCode.SIGNATURE_INVALID
        assert exc.value.status_code == 401


async def test_stock_read_rejects_a_merchant_sending_a_float() -> None:
    """Fail loud. Stock is int >= 0, never null, never a float — a Merchant
    sending otherwise has a bug the sidecar must not round away."""

    class FloatyMerchant(FakeMerchant):
        def stock_read(self, payload: dict[str, object]) -> dict[str, object]:
            return {"stock": {"SD-STICKERS": 4.5}}

    broken = FloatyMerchant()
    async with _client_for(broken) as c:
        with pytest.raises(TraitProtocolError, match="int >= 0"):
            await c.stock_read(["SD-STICKERS"])


# ── The bucketing rule, at the boundary ──────────────────────────────────────


@pytest.mark.parametrize(
    ("available", "threshold", "expected"),
    [
        (0, 3, AvailabilityBucket.SOLD_OUT),
        (1, 3, AvailabilityBucket.LOW_STOCK),
        (3, 3, AvailabilityBucket.LOW_STOCK),
        (4, 3, AvailabilityBucket.IN_STOCK),
        (100, 0, AvailabilityBucket.IN_STOCK),
    ],
)
def test_bucket_is_cut_at_the_items_own_threshold(
    available: int, threshold: int, expected: AvailabilityBucket
) -> None:
    assert buckets.bucket_for(available, threshold) is expected


def test_a_group_reads_in_stock_when_any_item_is() -> None:
    """Red in stock and black sold out is an in-stock group whose black variant
    refuses at the door — correct, and why stock is not evaluated at the group."""
    assert (
        buckets.bucket_for_group([AvailabilityBucket.SOLD_OUT, AvailabilityBucket.IN_STOCK])
        is AvailabilityBucket.IN_STOCK
    )
    assert (
        buckets.bucket_for_group([AvailabilityBucket.SOLD_OUT, AvailabilityBucket.LOW_STOCK])
        is AvailabilityBucket.LOW_STOCK
    )
    assert buckets.bucket_for_group([]) is AvailabilityBucket.SOLD_OUT


def test_the_oracle_path_refuses_to_leak_a_count_when_there_is_no_refusal() -> None:
    """The one place that puts a number in front of an agent exists to explain a
    refusal. Calling it otherwise leaks a count for nothing."""
    with pytest.raises(ValueError, match="not a refusal"):
        buckets.quantity_refusal_detail(available=10, requested=2)
    assert buckets.quantity_refusal_detail(available=2, requested=5) == (
        "Only 2 left; try 2 or fewer."
    )
    assert buckets.quantity_refusal_detail(available=0, requested=1) == "Sold out."


def test_negative_stock_reaching_the_bucketer_fails_loud() -> None:
    with pytest.raises(ValueError, match="int >= 0"):
        buckets.bucket_for(-1, 3)
