"""`openstore-conform` against a store that does implement the trait, and one
that does not.

**A conformance suite that has only ever seen conforming stores is untested.**
Both halves matter: it has to pass the fake, and it has to *catch* the specific
things implementers get wrong. So the failing cases here are real bugs, planted
one at a time — an oversell under concurrency, a salt handed out twice, a Quote
that carries a clock — and each one must produce a named failure rather than a
green run.
"""

from __future__ import annotations

import httpx
import pytest
from openstore.sidecar.conform.checks import Outcome, run
from openstore.sidecar.trait.fake import FakeMerchant, make_app
from openstore.sidecar.trait.seed import seeded

SECRET = "conformance-secret"  # noqa: S105 - a test secret, never a deployment one


async def _run_against(merchant: FakeMerchant, *, read_only: bool = False):  # type: ignore[no-untyped-def]
    """Run the suite against a fake served over real HTTP in-process.

    Over real HTTP rather than by calling the fake's methods: the suite is a
    client, and half of what it checks is on the wire.
    """
    transport = httpx.ASGITransport(app=make_app(merchant, SECRET))
    from openstore.sidecar.trait.client import TraitClient

    original = TraitClient.__init__

    def patched(self, base_url, secret, *, client=None):  # type: ignore[no-untyped-def]
        original(
            self,
            base_url,
            secret,
            client=httpx.AsyncClient(transport=transport, base_url=base_url),
        )

    TraitClient.__init__ = patched  # type: ignore[method-assign]
    try:
        return await run("http://merchant.internal", SECRET, read_only=read_only)
    finally:
        TraitClient.__init__ = original  # type: ignore[method-assign]


def _named(report, name: str):  # type: ignore[no-untyped-def]
    return next(c for c in report.checks if c.name == name)


# ── The conforming case ──────────────────────────────────────────────────────


async def test_the_fake_conforms() -> None:
    report = await _run_against(seeded())

    assert not report.fatal, report.fatal
    assert report.failures == [], [f"{c.door}: {c.name} — {c.detail}" for c in report.failures]
    assert report.exit_code == 0
    assert len(report.checks) >= 12


async def test_read_only_writes_nothing() -> None:
    """The first pass anybody should run against a production shop."""
    merchant = seeded()
    before = dict(merchant.stock)
    orders_before = len(merchant.orders)

    report = await _run_against(merchant, read_only=True)

    assert report.exit_code == 0
    assert merchant.stock == before, "a read-only run moved stock"
    assert len(merchant.orders) == orders_before, "a read-only run created an order"


async def test_a_run_gives_back_every_hold_it_takes() -> None:
    """A conformance run that quietly consumed a shop's inventory would be the
    last one anybody let near a real store."""
    merchant = seeded()
    before = dict(merchant.stock)

    await _run_against(merchant)

    assert (
        merchant.stock == before
    ), f"stock did not come back: {({k: (before[k], v) for k, v in merchant.stock.items() if before[k] != v})}"


# ── The bugs it has to catch ─────────────────────────────────────────────────


async def test_it_catches_a_store_that_oversells_under_concurrency() -> None:
    """The bug a single-threaded implementation passes every sequential test
    with: read the count, then decide."""

    class Oversells(FakeMerchant):
        def reserve(self, payload):  # type: ignore[no-untyped-def]
            # No compare-and-set: every caller is told yes, and the count is
            # decremented afterwards.
            for line in payload["lines"]:
                self.stock[line["sku"]] = self.stock.get(line["sku"], 0) - line["qty"]
            self.holds[payload["order_id"]] = list(payload["lines"])
            return {"reserved": True}

    merchant = seeded()
    broken = Oversells(**{f.name: getattr(merchant, f.name) for f in _fields(merchant)})
    broken.stock = dict.fromkeys(merchant.stock, 2)

    report = await _run_against(broken)

    check = _named(report, "concurrent reserves never oversell")
    assert check.outcome is Outcome.FAIL
    # Either signal is a correct catch: the count assertion, or the trait
    # client's own guard refusing a negative stock integer on the way in. The
    # second fires first here, which is the stronger of the two — a store that
    # reports -4 has already broken the contract before anyone counts holds.
    assert "compare-and-set" in check.detail or "is -" in check.detail
    assert report.exit_code == 1


async def test_it_catches_a_store_that_hands_the_salt_out_twice() -> None:
    """§6.3a: the salt is returned by `orders.create` and in no other response
    ever. A second door handing it out makes erasure meaningless."""

    class Leaks(FakeMerchant):
        def orders_read(self, payload):  # type: ignore[no-untyped-def]
            body = super().orders_read(payload)
            order = self.orders[payload["order_id"]]
            body["tracking_number"] = order.order_salt_hex
            return body

    merchant = seeded()
    broken = Leaks(**{f.name: getattr(merchant, f.name) for f in _fields(merchant)})

    report = await _run_against(broken)

    check = _named(report, "the salt is 128 bits, returned once")
    assert check.outcome is Outcome.FAIL
    assert "§6.3a" in check.detail or "order salt came back" in check.detail


async def test_it_catches_a_quote_that_carries_a_clock() -> None:
    """A date in a Quote turns every re-quote after midnight into a spurious
    `price-changed` — a Consumer told the price moved when it did not."""

    class Dated(FakeMerchant):
        def quote(self, payload):  # type: ignore[no-untyped-def]
            body = super().quote(payload)
            body["fulfillment_options"] = [
                {**option, "label": f"{option['label']} — arrives 2026-09-30"}
                for option in body["fulfillment_options"]
            ]
            return body

    merchant = seeded()
    broken = Dated(**{f.name: getattr(merchant, f.name) for f in _fields(merchant)})

    report = await _run_against(broken)

    check = _named(report, "an ETA is a day count, never a date")
    assert check.outcome is Outcome.FAIL
    assert "2026-09-30" in check.detail


async def test_it_catches_a_release_that_accepts_a_hold_nobody_took() -> None:
    """Accepting it would make the sidecar's escrow-zero invariant close on an
    entry that balances nothing."""

    class Lenient(FakeMerchant):
        def release(self, payload):  # type: ignore[no-untyped-def]
            self.holds.pop(payload["order_id"], None)
            return {"released": True}

    merchant = seeded()
    broken = Lenient(**{f.name: getattr(merchant, f.name) for f in _fields(merchant)})

    report = await _run_against(broken)

    check = _named(report, "releasing nothing refuses no-hold")
    assert check.outcome is Outcome.FAIL


async def test_an_unreachable_store_is_exit_two_not_a_failure() -> None:
    """ "I could not reach it" and "it is wrong" are different answers, and a
    deploy gate needs to tell them apart."""
    report = await run("http://127.0.0.1:1/nothing-here", SECRET)

    assert report.fatal
    assert report.exit_code == 2
    assert report.checks == []


async def test_a_wrong_secret_says_so_rather_than_failing_every_door() -> None:
    merchant = seeded()
    transport = httpx.ASGITransport(app=make_app(merchant, SECRET))
    from openstore.sidecar.trait.client import TraitClient

    original = TraitClient.__init__

    def patched(self, base_url, secret, *, client=None):  # type: ignore[no-untyped-def]
        original(
            self,
            base_url,
            secret,
            client=httpx.AsyncClient(transport=transport, base_url=base_url),
        )

    TraitClient.__init__ = patched  # type: ignore[method-assign]
    try:
        report = await run("http://merchant.internal", "not-the-secret")
    finally:
        TraitClient.__init__ = original  # type: ignore[method-assign]

    assert report.exit_code == 2
    assert "secret" in report.fatal


def _fields(merchant: FakeMerchant):  # type: ignore[no-untyped-def]
    from dataclasses import fields

    return [f for f in fields(merchant) if f.init]


@pytest.fixture(autouse=True)
def _quiet_httpx() -> None:
    """The suite deliberately makes refused calls; their tracebacks are noise."""
    return None
