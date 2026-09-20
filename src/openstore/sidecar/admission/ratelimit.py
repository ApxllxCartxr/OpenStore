"""Rate limits per §16.8. Policy caps bound an order; limits bound an attacker.

Two dimensions, because they stop different things: per-agent limits stop one
agent hammering, per-IP limits stop one host running many agents. A
self-registered stranger sits in the low tier and an allowlisted agent in the
high one — **reputation buys throughput only**. No tier unlocks money.

The separate throttles matter as much as the route limits. Tap-token issuance,
approve attempts and discount-code attempts are each their own bucket, because
each is its own oracle: door 9 would otherwise answer "is this a code?" all day.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum, unique

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.trait.errors import TraitError


@unique
class Tier(StrEnum):
    SELF_REGISTERED = "self-registered"
    ALLOWLISTED = "allowlisted"


#: (limit, window_seconds), straight from §16.8.
LIMITS: dict[str, dict[Tier, tuple[int, int]]] = {
    "agent": {Tier.SELF_REGISTERED: (30, 60), Tier.ALLOWLISTED: (300, 60)},
    "ip": {Tier.SELF_REGISTERED: (120, 60), Tier.ALLOWLISTED: (120, 60)},
    "profile-registration": {Tier.SELF_REGISTERED: (5, 3600), Tier.ALLOWLISTED: (5, 3600)},
    "tap-issuance": {Tier.SELF_REGISTERED: (5, 60), Tier.ALLOWLISTED: (5, 60)},
    "approve-attempt": {Tier.SELF_REGISTERED: (10, 3600), Tier.ALLOWLISTED: (10, 3600)},
    "code-attempt": {Tier.SELF_REGISTERED: (5, 3600), Tier.ALLOWLISTED: (5, 3600)},
    # The quantity-refusal oracle: the one path that names an exact count to an
    # agent, counted and capped like the oracle it is (SPEC §5).
    "quantity-oracle": {Tier.SELF_REGISTERED: (20, 3600), Tier.ALLOWLISTED: (20, 3600)},
}


@dataclass
class RateLimiter:
    """Sliding window. Exceeding any limit refuses `rate-limited`."""

    _hits: dict[tuple[str, str], deque[float]] = field(default_factory=dict)

    def check(
        self,
        bucket: str,
        subject: str,
        *,
        tier: Tier = Tier.SELF_REGISTERED,
        now: float | None = None,
    ) -> None:
        limit, window = LIMITS[bucket][tier]
        moment = now if now is not None else time.monotonic()
        key = (bucket, subject)
        hits = self._hits.setdefault(key, deque())

        while hits and moment - hits[0] > window:
            hits.popleft()

        if len(hits) >= limit:
            raise TraitError(
                ReasonCode.RATE_LIMITED,
                # Names the limit, not how close they are to it: a countdown is
                # a tuning signal for whoever is probing.
                f"too many requests; this limit is {limit} per {window}s",
                {"bucket": bucket},
            )
        hits.append(moment)

    def count(self, bucket: str, subject: str) -> int:
        return len(self._hits.get((bucket, subject), ()))
