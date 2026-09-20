"""Structured money-path logging.

**Transcripts are forensic; logs are operational.** They answer different
questions and neither replaces the other: a Transcript tells you what was
decided and why, byte-for-byte, forever. A log line tells you that the decision
took 840ms at 02:14 and that four before it refused for the same reason — which
is what a Merchant at 2am actually needs (SPEC §14).

Every money-path decision emits one line: order, agent, consumer, reason code,
duration. **Never secrets, never PII plaintext.** The redaction here is a deny
list by key name rather than a hopeful convention, because the one time it
matters is the time somebody passed a whole order row in by accident.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pythonjsonlogger import jsonlogger

LOGGER_NAME = "openstore.money"

#: Never logged, whatever the caller passes. Names rather than heuristics: a
#: regex over values would miss `line1` and flag a SKU.
REDACTED_KEYS = frozenset(
    {
        "destination",
        "contact",
        "email",
        "phone",
        "line1",
        "line2",
        "postal_code",
        "payer_handle",
        "vpa",
        "order_salt",
        "order_salt_hex",
        "signature",
        "client_secret",
        "hmac_secret",
        "passphrase",
        "token",
        "authorization",
    }
)

REDACTION = "[redacted]"


def redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop anything on the deny list, at any depth."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in REDACTED_KEYS:
            out[key] = REDACTION
        elif isinstance(value, dict):
            out[key] = redact(value)
        else:
            out[key] = value
    return out


def configure(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        # `python-json-logger` ships no py.typed marker, so its constructor is
        # untyped to mypy --strict. Narrowed here rather than loosened globally.
        formatter: logging.Formatter = jsonlogger.JsonFormatter(  # type: ignore[no-untyped-call]
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def money_event(
    event: str,
    *,
    order_id: str,
    agent_id: str = "",
    consumer_id: str = "",
    reason_code: str | None = None,
    duration_ms: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the line. Returned as well as logged so tests can assert its shape
    without scraping stderr."""
    payload: dict[str, Any] = {
        "event": event,
        "order_id": order_id,
        "agent_id": agent_id,
        # Already a per-domain pseudonym; the plaintext handle never reaches here.
        "consumer_id": consumer_id,
    }
    if reason_code:
        payload["reason_code"] = reason_code
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    payload.update(extra)

    safe = redact(payload)
    configure().info(event, extra={"money": safe})
    return safe


@contextmanager
def timed(event: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Emit one line with a duration, whatever happens.

    The refusal path is the one that matters most here: a Gate that refuses in
    3ms and one that refuses in 3s after a slow Merchant look identical in a
    Transcript.
    """
    started = time.perf_counter()
    result: dict[str, Any] = {}
    try:
        yield result
    finally:
        elapsed = int((time.perf_counter() - started) * 1000)
        money_event(event, duration_ms=elapsed, **{**fields, **result})
