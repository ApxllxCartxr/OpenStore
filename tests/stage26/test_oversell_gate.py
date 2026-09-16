# tests/stage26/test_oversell_gate.py
# Stage 26.3: stock:1 -> the second buyer is refused with
# inventory.insufficient_stock; tracked=false and unmanaged bypass; the
# compiler transcript gains no stock row (DECISION-047: byte-identical).

from __future__ import annotations

import pytest
from conftest import CART_ONE_VANILLA, MERCHANT, seed_tracked
from openstore.core.api import CommerceError, create_checkout_from_policy
from openstore.core.inventory import compute_sku_exposure

SKU = "gelato_vanilla"


def _checkout(session, config, policy, cart, checkout_id: str):
    return create_checkout_from_policy(
        config=config,
        session=session,
        trace_id=f"trace_{checkout_id}",
        client_id="cli_gate",
        merchant_id=MERCHANT,
        cart_items=cart,
        cart_hash=f"hash_{checkout_id}",
        cart_version=1,
        policy=policy,
        assertion_verified=True,
        idempotency_key=checkout_id,
    )


def test_second_buyer_refused(session, config, policy):
    seed_tracked(session, SKU, 1)
    first = _checkout(session, config, policy, CART_ONE_VANILLA, "chk_gate_1")
    assert first.allowed is True
    session.commit()
    assert compute_sku_exposure(session, MERCHANT, SKU) == 1
    with pytest.raises(CommerceError) as exc:
        _checkout(session, config, policy, CART_ONE_VANILLA, "chk_gate_2")
    assert exc.value.reason_code == "inventory.insufficient_stock"
    session.rollback()
    # The refused checkout reserved nothing.
    assert compute_sku_exposure(session, MERCHANT, SKU) == 1


def test_multi_unit_gate(session, config, policy):
    seed_tracked(session, SKU, 3)
    cart = [{"sku": SKU, "qty": 4, "unit_minor": 15000, "tags": []}]
    with pytest.raises(CommerceError) as exc:
        _checkout(session, config, policy, cart, "chk_gate_multi")
    assert exc.value.reason_code == "inventory.insufficient_stock"


def test_tracked_false_bypasses_gate(session, config, policy):
    seed_tracked(session, SKU, 0, tracked=False)
    result = _checkout(session, config, policy, CART_ONE_VANILLA, "chk_gate_mto")
    assert result.allowed is True


def test_unmanaged_bypasses_gate(session, config, policy):
    result = _checkout(
        session, config, policy,
        [{"sku": "gelato_unmanaged", "qty": 50, "unit_minor": 12000, "tags": []}],
        "chk_gate_unmanaged",
    )
    assert result.allowed is True


def test_compiler_transcript_has_no_stock_row(session, config, policy):
    """DECISION-047: the gate lives pre-compiler, so the transcript keeps its
    canonical 12-check shape — the offline verifier replays exactly this."""
    seed_tracked(session, SKU, 10)
    result = _checkout(session, config, policy, CART_ONE_VANILLA, "chk_gate_txn")
    assert result.allowed is True
    names = [row["check"] for row in result.transcript]
    assert names == [
        "human_authority_present",
        "currency_match",
        "merchant_lock",
        "policy_not_before",
        "policy_expiry",
        "transaction_count",
        "item_qty",
        "item_blocked_sku",
        "item_tag_allowlist",
        "spend_per_tx",
        "spend_envelope",
        "spend_cumulative",
        "campaign_validity",
    ]


def test_compiler_stays_pinned(session, config, policy):
    """The stage-24 digest sentinel's invariant, restated at the gate: stock
    logic must never leak into compiler.py (same assertion as
    scripts/pin_compiler_digest.py --check, without the subprocess)."""
    from pathlib import Path

    from openstore.core.compiler import get_compiler_digest

    checks = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openstore"
        / "verify"
        / "checks.py"
    ).read_text()
    assert get_compiler_digest() in checks
