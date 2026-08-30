import time
from datetime import datetime, timedelta

from sqlmodel import Session, select

from reference.merchant.models import SpendLedgerEntry


class TokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        self.capacity = capacity
        self.tokens = float(capacity)
        self.refill_rate = refill_rate_per_sec
        self.last_refill = time.monotonic()

    def try_consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


_BUCKETS: dict[tuple[str, str], TokenBucket] = {}

BUCKET_CONFIG = {
    "search_products": (20, 2.0),
    "get_product": (20, 2.0),
    "create_cart": (10, 1.0),
    "update_cart": (10, 1.0),
    "checkout_initiate": (3, 0.05),
    "get_signed_mandate": (3, 0.05),
    "checkout_confirm": (3, 0.05),
}


def check_rate_limit(client_id: str, tool: str) -> bool:
    key = (client_id, tool)
    if key not in _BUCKETS:
        capacity, refill = BUCKET_CONFIG.get(tool, (10, 1.0))
        _BUCKETS[key] = TokenBucket(capacity, refill)
    return _BUCKETS[key].try_consume()


def rolling_spend_minor(session: Session, client_id: str, window_hours: int = 24) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    entries = session.exec(
        select(SpendLedgerEntry)
        .where(SpendLedgerEntry.client_id == client_id)
        .where(SpendLedgerEntry.created_at >= cutoff)
    ).all()
    return sum(e.amount_minor for e in entries)
