"""The expiry sweeper — the thing that makes `expires_at` mean something.

`gate/lifecycle.py` has always known *what* should happen to an order that
passes its deadline, and nothing ever asked it. Pending checkouts lived in
memory, the 24h window was a sentence in the spec, and an abandoned tap held
Merchant stock until the process restarted. This is the loop that runs
`lifecycle.sweep` over the live checkouts every 60 seconds (§16.7).

Three deadlines, one column, and they do genuinely different things:

- **`pending` at 24h** — the Quote's validity. Nothing was held, so nothing is
  returned: the order moves to `expired` and its approve link stops opening.
- **`confirmed` prepaid at the payment window** — stock *is* held, so this
  releases it through door 5 and closes the Ledger hold with a `RELEASE` before
  the status moves. An abandoned tap holding inventory is an availability hole
  any stranger can open at will, and it is the reason this loop exists.
- **`confirmed` COD at 7 days** — alerts and never acts. A parcel that is late
  is not a parcel that is lost, and only the Merchant knows which.

**The Merchant is the serializer, not a lock here.** Door 8 keys on
`order_id:attempt`, so a tap that moved an order to `confirmed` while this loop
was awaiting makes the expiry transition refuse — and the sweeper then re-reads
Merchant truth and corrects its own copy rather than logging an error about it.
That is the whole concurrency story: two writers, one key, and the Merchant
decides.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from openstore.sidecar.checkout import CheckoutContext, Pending
from openstore.sidecar.core.codes import OrderStatus, ReasonCode
from openstore.sidecar.gate.lifecycle import Deadline, next_deadline, sweep
from openstore.sidecar.ledger.entries import Ledger

#: §16.7. An order that expired 59 seconds ago is still visible as overdue
#: rather than silently held, which is the property the interval buys.
SWEEP_INTERVAL_SECONDS = 60

_log = logging.getLogger("openstore.sweeper")


@dataclass(frozen=True)
class _Due:
    """A deadline that has passed, and what `lifecycle` says to do about it."""

    deadline: Deadline
    alert_only: bool


@dataclass(frozen=True)
class Swept:
    """What one pass did to one order, for the log and for the tests."""

    order_id: str
    reason_code: ReasonCode
    to_status: OrderStatus | None
    released_stock: bool
    alerted: bool


def deadline_for(checkout: Pending) -> Deadline | None:
    """This checkout's next deadline, or `None` if it has none.

    A terminal or post-money order has no deadline the sidecar will act on —
    `lifecycle.next_deadline` returns `None` for every status outside
    `SWEEPABLE`, and that is the only place the rule lives.
    """
    return next_deadline(
        checkout.status,
        checkout.method,
        created_at=checkout.created_at,
        confirmed_at=checkout.confirmed_at,
        link_expires_at=checkout.link_expires_at,
    )


def _action_for(checkout: Pending, *, now: datetime) -> _Due | None:
    """Whether this checkout is due, per `lifecycle` and nothing else."""
    deadline = deadline_for(checkout)
    if deadline is None:
        return None
    action = sweep(checkout.order_id, checkout.status, checkout.method, deadline, now=now)
    if action is None:
        return None
    return _Due(deadline=deadline, alert_only=action.alert_only)


async def sweep_once(ctx: CheckoutContext, *, now: datetime | None = None) -> list[Swept]:
    """One pass over every live checkout. Never raises.

    A sweeper that dies on one wedged order stops sweeping every other one, so
    each order is handled inside its own guard and a failure leaves that order
    exactly where it was — still overdue, still visible, swept again in 60
    seconds.
    """
    moment = now or datetime.now(UTC)
    done: list[Swept] = []

    # Read once at the top of the pass. Each order is then re-confirmed against
    # the Merchant below before anything is done to it, so a tap that lands
    # mid-pass is caught by that read rather than by this one.
    for checkout in await ctx.store.live():
        action = _action_for(checkout, now=moment)
        if action is None:
            continue

        # **Confirm against the Merchant before acting, never after.** The
        # in-memory status is a copy, and a tap that landed between two passes
        # moves the order at the shop and not here. Acting on the stale copy
        # applies the *wrong deadline*: a `confirmed` order swept as `pending`
        # expires without releasing the stock it is holding, which is the exact
        # failure this loop exists to prevent. One extra door-8 read, and only
        # for an order already past a deadline.
        try:
            await _resync(ctx, checkout)
        except Exception as exc:  # noqa: BLE001 - an unreachable shop is not this order's fault
            _log.warning("could not confirm %s before sweeping: %s", checkout.order_id, exc)
            continue
        action = _action_for(checkout, now=moment)
        if action is None:
            continue
        deadline = action.deadline

        try:
            if action.alert_only:
                done.append(_alert(checkout, deadline))
            else:
                done.append(await expire(ctx, checkout, deadline))
        except Exception as exc:  # noqa: BLE001 - one wedged order must not stop the pass
            _log.warning(
                "sweep failed for %s at %s: %s", checkout.order_id, checkout.status.value, exc
            )
            with contextlib.suppress(Exception):
                await _resync(ctx, checkout)

    # The passkey working set ages out on the same pass. Without this, "a
    # credential is kept for the purchase it was enrolled for and then it is
    # gone" would be a sentence in a docstring rather than something that
    # happens — and durable credentials that never expire are the account
    # system v1 deliberately does not have.
    if ctx.passkey_rp is not None and ctx.passkey_rp.sessionmaker is not None:
        try:
            forgotten = await ctx.passkey_rp.forget_expired(now=moment)
        except Exception as exc:  # noqa: BLE001 - housekeeping must not stop the pass
            _log.warning("could not sweep the passkey working set: %s", exc)
        else:
            if forgotten:
                _log.warning("forgot %d expired passkey row(s)", forgotten)
    return done


def _alert(checkout: Pending, deadline: Deadline) -> Swept:
    """The COD delivery window. Visible in `/agentic` within one interval, and
    nothing is cancelled — SPEC §14's whole answer to a wedged sidecar is that
    somebody can see it."""
    from openstore.sidecar.console.routes import record_overdue_hold

    record_overdue_hold(checkout.order_id, checkout.status.value, deadline.at, checkout.total_minor)
    return Swept(
        order_id=checkout.order_id,
        reason_code=deadline.reason_code,
        to_status=None,
        released_stock=False,
        alerted=True,
    )


async def expire(ctx: CheckoutContext, checkout: Pending, deadline: Deadline) -> Swept:
    """Expire one order, releasing whatever it holds first.

    **Order matters and is the same as `cancel`'s:** stock goes back before the
    status moves, so a crash between the two leaves an order that still looks
    live rather than an expired one holding inventory nobody can buy.
    """
    from openstore.sidecar import checkout as flow

    released = False
    if deadline.releases_stock:
        assert ctx.trait is not None
        await ctx.trait.release(checkout.order_id)
        released = True
        if ctx.sessionmaker is not None:
            from openstore.sidecar.core.db import session_scope

            async with session_scope(ctx.sessionmaker) as session:
                ledger = Ledger(session)
                held = (await ledger.position(checkout.order_id)).open_holds_minor
                if held > 0:
                    # Exactly its own hold, like `cancel`: releasing a different
                    # amount closes escrow-zero on an entry that balances nothing.
                    await ledger.release(checkout.order_id, held, checkout.quote.currency)

    await flow.set_status(ctx, checkout.order_id, OrderStatus.EXPIRED, deadline.reason_code.value)
    checkout.status = OrderStatus.EXPIRED

    if checkout.link_id:
        await ctx.provider.cancel(checkout.link_id)
        checkout.link_id = ""
    # The approve link dies with the window it was minted for. Leaving it open
    # would render a page for an order the Merchant has already expired.
    checkout.tap_token = ""
    await ctx.store.save(checkout)

    _log.warning(
        "expired %s (%s) after %s; stock %s",
        checkout.order_id,
        checkout.method.value,
        deadline.reason_code.value,
        "released" if released else "was never held",
    )
    return Swept(
        order_id=checkout.order_id,
        reason_code=deadline.reason_code,
        to_status=OrderStatus.EXPIRED,
        released_stock=released,
        alerted=False,
    )


async def _resync(ctx: CheckoutContext, checkout: Pending) -> None:
    """Correct this copy from Merchant truth.

    The usual cause of a difference is not an error at all: a Consumer tapped
    while this pass was elsewhere and the Merchant took that transition. Reading
    it back is the only honest move — guessing would put a status the Merchant
    never took in front of an agent, and acting on it would apply a deadline
    belonging to a status the order has already left.
    """
    if ctx.trait is None:
        return
    order = await ctx.trait.orders_read(checkout.order_id)
    if order.status is not checkout.status:
        _log.warning(
            "resynced %s: %s here, %s at the shop",
            checkout.order_id,
            checkout.status.value,
            order.status.value,
        )
        checkout.status = order.status
        # Written back, so the next pass reads the corrected status rather than
        # rediscovering the same drift — and so `/agentic` shows what the shop
        # says rather than what this process last assumed.
        await ctx.store.save(checkout)


async def run_forever(
    ctx: CheckoutContext, *, interval_seconds: int = SWEEP_INTERVAL_SECONDS
) -> None:
    """The background loop. Cancelled at shutdown; never exits on its own."""
    _log.warning("expiry sweeper started: every %ss", interval_seconds)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            swept = await sweep_once(ctx)
        except Exception as exc:  # noqa: BLE001 - the loop outlives any one pass
            _log.warning("sweep pass failed entirely: %s", exc)
            continue
        if swept:
            _log.warning("sweep acted on %d order(s)", len(swept))
        # Deliveries ride the same tick rather than a second loop: they are the
        # same kind of work — something due, retried with a backoff — and one
        # timer is one thing an operator has to reason about.
        try:
            sent, parked = await drain_events(ctx)
        except Exception as exc:  # noqa: BLE001 - the loop outlives any one pass
            _log.warning("event drain failed entirely: %s", exc)
            continue
        if sent or parked:
            _log.warning("events: %d delivered, %d parked", sent, parked)


async def drain_events(ctx: CheckoutContext) -> tuple[int, int]:
    """Deliver every event that is due. Returns (delivered, parked).

    Never raises for one bad endpoint: an agent whose callback is down must not
    stop delivery to every other agent, so each send is guarded and a failure is
    a backoff on that row alone.
    """
    from openstore.sidecar.evidence.keys import sign
    from openstore.sidecar.protocols.agent_routes import get_surface
    from openstore.sidecar.protocols.events import deliver, signing_payload

    surface = get_surface()
    if not surface.events.enabled or surface.keyring is None:
        # No keyring means nothing can be signed, and an unsigned event is one
        # an agent has no reason to believe. Not sending is the honest outcome.
        return 0, 0

    delivered = parked = 0
    for event in await surface.events.due():
        try:
            await deliver(
                event,
                signature=sign(surface.keyring.current, signing_payload(event.body)),
                kid=surface.keyring.current.kid,
                fetcher=surface.fetcher,
            )
        except Exception as exc:  # noqa: BLE001 - one dead endpoint is not the queue
            state = await surface.events.failed(event.event_id, event.attempts + 1, str(exc))
            parked += state == "parked"
            _log.warning(
                "event %s to %s failed (attempt %d): %s",
                event.event_id,
                event.agent_id,
                event.attempts + 1,
                exc,
            )
            continue
        await surface.events.delivered(event.event_id)
        delivered += 1
    return delivered, parked
