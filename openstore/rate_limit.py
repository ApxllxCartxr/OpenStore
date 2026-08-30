"""Database-backed rate limiting (PRODUCTION_READINESS §0.8).

Token bucket per (client_id, tool) stored in database.
Survives restarts and works across workers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Header, Request
from sqlmodel import Session, select

from openstore.errors import RATELIMIT_EXCEEDED, RateLimitError, error_envelope
from openstore.models import RateLimitBucket


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    """Rate limit configuration for a tool."""
    capacity: int  # max tokens
    refill_rate: float  # tokens per second


# Default rate limits per tool
DEFAULT_LIMITS = {
    "search_products": RateLimitConfig(capacity=30, refill_rate=1.0),
    "get_product": RateLimitConfig(capacity=60, refill_rate=2.0),
    "create_cart": RateLimitConfig(capacity=10, refill_rate=0.5),
    "update_cart": RateLimitConfig(capacity=20, refill_rate=1.0),
    "checkout_initiate": RateLimitConfig(capacity=5, refill_rate=0.2),
    "checkout_confirm": RateLimitConfig(capacity=5, refill_rate=0.2),
    "submit_mandate": RateLimitConfig(capacity=10, refill_rate=0.5),
    "webauthn_register_begin": RateLimitConfig(capacity=10, refill_rate=0.2),
    "webauthn_register_complete": RateLimitConfig(capacity=10, refill_rate=0.2),
    "webauthn_begin_assertion": RateLimitConfig(capacity=10, refill_rate=0.2),
    "webauthn_complete_assertion": RateLimitConfig(capacity=10, refill_rate=0.2),
}


class RateLimiter:
    """Database-backed token bucket rate limiter."""

    def __init__(self, engine, limits: Optional[dict] = None):
        self.engine = engine
        self.limits = limits or DEFAULT_LIMITS

    def _get_bucket(self, session: Session, client_id: str, tool: str) -> RateLimitBucket:
        """Get or create rate limit bucket."""
        bucket = session.exec(
            select(RateLimitBucket).where(
                RateLimitBucket.client_id == client_id,
                RateLimitBucket.tool == tool,
            )
        ).first()

        if bucket is None:
            config = self.limits.get(tool, RateLimitConfig(capacity=10, refill_rate=0.5))
            bucket = RateLimitBucket(
                client_id=client_id,
                tool=tool,
                tokens=config.capacity,
                capacity=config.capacity,
                refill_rate=config.refill_rate,
                last_refill=int(time.time()),
            )
            session.add(bucket)
            session.commit()
            session.refresh(bucket)

        return bucket

    def _refill(self, bucket: RateLimitBucket) -> None:
        """Refill tokens based on elapsed time."""
        now = int(time.time())
        elapsed = now - bucket.last_refill
        if elapsed > 0:
            new_tokens = elapsed * bucket.refill_rate
            bucket.tokens = min(bucket.capacity, bucket.tokens + new_tokens)
            bucket.last_refill = now

    def check_limit(self, client_id: str, tool: str, cost: int = 1) -> bool:
        """Check if request is allowed. Returns True if allowed, raises RateLimitError if not."""
        with Session(self.engine) as session:
            bucket = self._get_bucket(session, client_id, tool)
            self._refill(bucket)

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                session.add(bucket)
                session.commit()
                return True

            # Calculate retry-after
            tokens_needed = cost - bucket.tokens
            retry_after = int(tokens_needed / bucket.refill_rate) + 1

            raise RateLimitError(
                message=f"Rate limit exceeded for {tool}",
                details={
                    "tool": tool,
                    "client_id": client_id,
                    "retry_after": retry_after,
                    "capacity": bucket.capacity,
                    "refill_rate": bucket.refill_rate,
                },
            )

    def get_status(self, client_id: str, tool: str) -> dict:
        """Get current rate limit status."""
        with Session(self.engine) as session:
            bucket = self._get_bucket(session, client_id, tool)
            self._refill(bucket)
            return {
                "tool": tool,
                "client_id": client_id,
                "tokens_remaining": bucket.tokens,
                "capacity": bucket.capacity,
                "refill_rate": bucket.refill_rate,
            }

    def reset(self, client_id: str, tool: str) -> None:
        """Reset a client's bucket (admin operation)."""
        with Session(self.engine) as session:
            bucket = session.exec(
                select(RateLimitBucket).where(
                    RateLimitBucket.client_id == client_id,
                    RateLimitBucket.tool == tool,
                )
            ).first()
            if bucket:
                config = self.limits.get(tool, RateLimitConfig(capacity=10, refill_rate=0.5))
                bucket.tokens = config.capacity
                bucket.last_refill = int(time.time())
                session.add(bucket)
                session.commit()


# ---------- FastAPI Dependency ----------


async def rate_limit_dependency(
    tool: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> None:
    """FastAPI dependency to enforce rate limits."""
    # Extract client_id from OAuth token or API key
    client_id = "anonymous"
    if authorization and authorization.startswith("Bearer "):
        # In a real implementation, decode the token to get client_id
        client_id = f"token_{hash(authorization) % 10000}"

    rate_limiter: RateLimiter = request.app.state.rate_limiter
    rate_limiter.check_limit(client_id, tool)