"""HMAC over the raw body, with a nonce and a 60-second window (§6.1, §16.7).

Signed over the raw bytes, never over a re-serialization: two JSON encoders
disagree about key order and whitespace, and a signature that covers a
round-trip covers nothing.

The window plus the nonce is what stops replay. Neither alone does: a window
without a nonce lets an attacker resend inside it, and a nonce without a window
means remembering every nonce ever seen.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

SIGNATURE_HEADER = "X-OpenStore-Signature"
TIMESTAMP_HEADER = "X-OpenStore-Timestamp"
NONCE_HEADER = "X-OpenStore-Nonce"
IDEMPOTENCY_HEADER = "Idempotency-Key"

REPLAY_WINDOW_SECONDS = 60


def sign(secret: str, *, body: bytes, timestamp: int, nonce: str, path: str) -> str:
    """The signature covers the path as well as the body.

    Without the path, a signed `release` body is a signed `commit` body: the
    two carry the same `{order_id}` shape, and an attacker who can redirect a
    request gets a free close of somebody's hold.
    """
    preimage = b"\n".join(
        [path.encode("utf-8"), str(timestamp).encode("ascii"), nonce.encode("ascii"), body]
    )
    return hmac.new(secret.encode("utf-8"), preimage, hashlib.sha256).hexdigest()


def new_nonce() -> str:
    return secrets.token_hex(16)


@dataclass
class ReplayGuard:
    """Remembers nonces for exactly as long as the window they are valid in.

    Not a cache with an eviction policy — an unbounded set of nonces is a memory
    leak an attacker controls the size of.
    """

    window_seconds: int = REPLAY_WINDOW_SECONDS
    _seen: dict[str, int] = field(default_factory=dict)

    def check_and_remember(self, nonce: str, timestamp: int, now: int) -> None:
        if abs(now - timestamp) > self.window_seconds:
            raise ValueError(
                f"timestamp {timestamp} is outside the {self.window_seconds}s replay window "
                f"(now {now})"
            )
        self._prune(now)
        if nonce in self._seen:
            raise ValueError(f"nonce {nonce} has already been used")
        self._seen[nonce] = timestamp

    def _prune(self, now: int) -> None:
        stale = [n for n, ts in self._seen.items() if abs(now - ts) > self.window_seconds]
        for nonce in stale:
            del self._seen[nonce]


def verify(
    secret: str,
    *,
    body: bytes,
    path: str,
    signature: str,
    timestamp: str,
    nonce: str,
    guard: ReplayGuard,
    now: int | None = None,
) -> None:
    """Raise on anything that is not a valid, fresh, unseen signature.

    `compare_digest`, not `==`: a byte-by-byte comparison leaks the correct
    prefix through timing, and the secret is the Merchant's.
    """
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise ValueError(f"unparseable timestamp {timestamp!r}") from None

    expected = sign(secret, body=body, timestamp=ts, nonce=nonce, path=path)
    if not hmac.compare_digest(expected, signature or ""):
        raise ValueError("signature does not verify")

    guard.check_and_remember(nonce, ts, now if now is not None else int(time.time()))
