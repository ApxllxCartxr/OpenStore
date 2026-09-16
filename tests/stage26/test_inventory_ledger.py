# tests/stage26/test_inventory_ledger.py
# Stage 26.2: core/inventory.py mirrors core/ledger.py — idempotent movements,
# reserved-nets-to-zero at terminal states, exposure math.

from __future__ import annotations

import pytest
from conftest import MERCHANT, seed_tracked
from openstore.core.inventory import (
    available_qty,
    check_stock_available,
    committed_for_checkout,
    compute_sku_exposure,
    create_commit_entries,
    create_release_entries,
    create_reserve_entries,
    create_restock_entries,
    outstanding_for_checkout,
    verify_inventory_balances,
)
from openstore.models import InventoryLedgerEntry
from sqlmodel import select

SKU = "gelato_vanilla"


def _count(session) -> int:
    return len(session.exec(select(InventoryLedgerEntry)).all())


def test_reserve_commit_balances(session):
    seed_tracked(session, SKU, 10)
    create_reserve_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    assert compute_sku_exposure(session, MERCHANT, SKU) == 2
    assert outstanding_for_checkout(session, "chk_1", SKU) == 2
    create_commit_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    assert compute_sku_exposure(session, MERCHANT, SKU) == 0
    assert outstanding_for_checkout(session, "chk_1", SKU) == 0
    assert committed_for_checkout(session, "chk_1", SKU) == 2
    assert verify_inventory_balances(session, "chk_1") is True


def test_reserve_release_balances(session):
    seed_tracked(session, SKU, 10)
    create_reserve_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 3)
    create_release_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 3)
    assert compute_sku_exposure(session, MERCHANT, SKU) == 0
    assert verify_inventory_balances(session, "chk_1") is True


def test_commit_restock_balances(session):
    seed_tracked(session, SKU, 10)
    create_reserve_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    create_commit_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    create_restock_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    assert committed_for_checkout(session, "chk_1", SKU) == 0
    assert verify_inventory_balances(session, "chk_1") is True


def test_commit_without_reserve_breaks_balance(session):
    """A COMMIT with no prior RESERVE is a broken ledger, never silent —
    the money path's INV-4 capture-without-reserve rule, transcribed."""
    seed_tracked(session, SKU, 10)
    create_commit_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    assert verify_inventory_balances(session, "chk_1") is False


def test_movements_are_idempotent(session):
    seed_tracked(session, SKU, 10)
    create_reserve_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    n = _count(session)
    create_reserve_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    assert _count(session) == n
    create_commit_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    n = _count(session)
    create_commit_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    assert _count(session) == n
    create_restock_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    n = _count(session)
    create_restock_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 2)
    assert _count(session) == n
    assert verify_inventory_balances(session, "chk_1") is True


def test_nonpositive_quantity_fails_loud(session):
    seed_tracked(session, SKU, 10)
    with pytest.raises(Exception):
        create_reserve_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 0)


def test_available_math_and_unmanaged(session):
    # No row at all: unmanaged, never coerced to 0.
    assert available_qty(session, MERCHANT, "gelato_unmanaged") is None
    assert check_stock_available(session, MERCHANT, "gelato_unmanaged", 999) is True
    # Tracked row, no platform truth yet: unmanaged too.
    seed_tracked(session, SKU, None)
    assert available_qty(session, MERCHANT, SKU) is None
    assert check_stock_available(session, MERCHANT, SKU, 999) is True


def test_tracked_false_bypasses(session):
    seed_tracked(session, SKU, 0, tracked=False)
    assert available_qty(session, MERCHANT, SKU) is None
    assert check_stock_available(session, MERCHANT, SKU, 999) is True


def test_exposure_subtracts_from_truth(session):
    seed_tracked(session, SKU, 10)
    assert available_qty(session, MERCHANT, SKU) == 10
    create_reserve_entries(session, "t1", "c1", "chk_1", MERCHANT, SKU, 4)
    assert available_qty(session, MERCHANT, SKU) == 6
    assert check_stock_available(session, MERCHANT, SKU, 6) is True
    assert check_stock_available(session, MERCHANT, SKU, 7) is False
