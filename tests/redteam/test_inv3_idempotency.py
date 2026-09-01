# tests/redteam/test_inv3_idempotency.py
# INV-3 — Idempotency is a contract, not a cache. Unique per (client_id, idempotency_key).
# Red-team: replay with a DIFFERENT payload, a DIFFERENT client_id, or a different
# trace must be REJECTED — never silently served a stale cached response.

from __future__ import annotations

import pytest
from openstore.core.idempotency import (
    IdempotencyError,
    check_idempotency,
    store_idempotency_result,
)


def test_same_key_same_payload_returns_cached(session):
    key = "idem:checkout.create:tr_1:cli_1:chk_1"
    body = {"cart": [{"sku": "A", "qty": 1, "unit_minor": 1000}]}
    store_idempotency_result(session, key, "tr_1", "cli_1", body, 201, {"id": "chk_1"})
    session.commit()

    result = check_idempotency(session, key, "tr_1", "cli_1", body)
    assert result is not None
    status, resp = result
    assert status == 201
    assert resp == {"id": "chk_1"}


def test_same_key_different_payload_rejected(session):
    key = "idem:checkout.create:tr_1:cli_1:chk_1"
    body_a = {"cart": [{"sku": "A", "qty": 1, "unit_minor": 1000}]}
    store_idempotency_result(session, key, "tr_1", "cli_1", body_a, 201, {"id": "chk_1"})
    session.commit()

    body_b = {"cart": [{"sku": "A", "qty": 99, "unit_minor": 999000}]}
    with pytest.raises(IdempotencyError) as ei:
        check_idempotency(session, key, "tr_1", "cli_1", body_b)
    assert ei.value.reason_code == "idempotency_request_mismatch"


def test_same_key_different_client_rejected(session):
    key = "idem:checkout.create:tr_1:cli_1:chk_1"
    body = {"cart": [{"sku": "A", "qty": 1, "unit_minor": 1000}]}
    store_idempotency_result(session, key, "tr_1", "cli_1", body, 201, {"id": "chk_1"})
    session.commit()

    with pytest.raises(IdempotencyError) as ei:
        check_idempotency(session, key, "tr_1", "cli_2", body)
    assert ei.value.reason_code == "idempotency_key_conflict"


def test_same_key_different_trace_rejected(session):
    key = "idem:checkout.create:tr_1:cli_1:chk_1"
    body = {"cart": [{"sku": "A", "qty": 1, "unit_minor": 1000}]}
    store_idempotency_result(session, key, "tr_1", "cli_1", body, 201, {"id": "chk_1"})
    session.commit()

    with pytest.raises(IdempotencyError) as ei:
        check_idempotency(session, key, "tr_2", "cli_1", body)
    assert ei.value.reason_code == "idempotency_key_conflict"


def test_store_conflicting_key_rejects(session):
    key = "idem:checkout.create:tr_1:cli_1:chk_1"
    body = {"cart": [{"sku": "A", "qty": 1, "unit_minor": 1000}]}
    store_idempotency_result(session, key, "tr_1", "cli_1", body, 201, {"id": "chk_1"})
    session.commit()

    with pytest.raises(IdempotencyError) as ei:
        store_idempotency_result(session, key, "tr_1", "cli_1", {"other": True}, 201, {"id": "x"})
    assert ei.value.reason_code == "idempotency_request_mismatch"


def test_expired_key_rejected(session):
    key = "idem:checkout.create:tr_1:cli_1:chk_1"
    body = {"cart": [{"sku": "A", "qty": 1, "unit_minor": 1000}]}
    store_idempotency_result(session, key, "tr_1", "cli_1", body, 201, {"id": "chk_1"},
                             ttl_seconds=0)
    session.commit()

    with pytest.raises(IdempotencyError) as ei:
        check_idempotency(session, key, "tr_1", "cli_1", body)
    assert ei.value.reason_code == "idempotency_expired"
