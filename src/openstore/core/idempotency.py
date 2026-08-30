# OpenStore core — idempotency contract (INV-3)

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from openstore.models import IdempotencyKey


class IdempotencyError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


IDEMPOTENCY_TTL_SECONDS = 86400 * 7  # 7 days


def compute_request_hash(request_body: dict[str, Any]) -> str:
    """Compute deterministic hash of request body for idempotency comparison."""
    # Sort keys for deterministic serialization
    serialized = json.dumps(request_body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()[:64]


def check_idempotency(
    session: Session,
    idempotency_key: str,
    trace_id: str,
    client_id: str,
    request_body: dict[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    """
    INV-3: Check idempotency key.

    Returns (status_code, response_body) if key exists and request matches.
    Returns None if key doesn't exist or request differs (caller must create new).
    Raises IdempotencyError if key exists but request differs (contract violation).
    """
    if not idempotency_key:
        return None

    existing = session.exec(
        select(IdempotencyKey).where(IdempotencyKey.key == idempotency_key)
    ).first()

    if not existing:
        return None

    # Key exists - verify it's for the same trace/client (security)
    if existing.trace_id != trace_id or existing.client_id != client_id:
        raise IdempotencyError(
            "idempotency_key_conflict",
            f"Idempotency key {idempotency_key} belongs to different trace/client"
        )

    # Verify request hasn't changed (contract)
    request_hash = compute_request_hash(request_body)
    if existing.request_hash != request_hash:
        raise IdempotencyError(
            "idempotency_request_mismatch",
            f"Idempotency key {idempotency_key} reused with different request body"
        )

    # Check expiry
    if datetime.utcnow() > existing.expires_at:
        raise IdempotencyError(
            "idempotency_expired",
            f"Idempotency key {idempotency_key} has expired"
        )

    return (existing.response_status, existing.response_body)


def store_idempotency_result(
    session: Session,
    idempotency_key: str,
    trace_id: str,
    client_id: str,
    request_body: dict[str, Any],
    response_status: int,
    response_body: dict[str, Any],
    ttl_seconds: int = IDEMPOTENCY_TTL_SECONDS,
) -> IdempotencyKey:
    """
    INV-3: Store idempotency result.

    Caller MUST call this after successful processing.
    The key is the contract - same key + same request = same response.
    """
    if not idempotency_key:
        raise IdempotencyError("idempotency_key_required", "Idempotency key is required")

    request_hash = compute_request_hash(request_body)
    now = datetime.utcnow()

    # Check if already exists (should have been caught by check_idempotency)
    existing = session.exec(
        select(IdempotencyKey).where(IdempotencyKey.key == idempotency_key)
    ).first()

    if existing:
        if existing.request_hash != request_hash:
            raise IdempotencyError(
                "idempotency_request_mismatch",
                f"Idempotency key {idempotency_key} reused with different request body"
            )
        # Already stored with same request - verify response matches
        if existing.response_status != response_status or existing.response_body != response_body:
            raise IdempotencyError(
                "idempotency_response_mismatch",
                f"Idempotency key {idempotency_key} would return different response"
            )
        return existing

    entry = IdempotencyKey(
        key=idempotency_key,
        trace_id=trace_id,
        client_id=client_id,
        request_hash=request_hash,
        response_status=response_status,
        response_body=response_body,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )

    session.add(entry)
    session.flush()

    return entry


def generate_idempotency_key(operation: str, trace_id: str, client_id: str, reference: str) -> str:
    """Generate deterministic idempotency key for an operation."""
    return f"idem:{operation}:{trace_id}:{client_id}:{reference}"


def cleanup_expired_idempotency_keys(session: Session) -> int:
    """Clean up expired idempotency keys (maintenance task)."""
    now = datetime.utcnow()
    expired = session.exec(
        select(IdempotencyKey).where(IdempotencyKey.expires_at < now)
    ).all()

    count = len(expired)
    for entry in expired:
        session.delete(entry)

    session.flush()
    return count
