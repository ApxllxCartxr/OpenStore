# tests/sentinel/test_enum_exhaustiveness.py
# S10.3 — Enum exhaustiveness vs REGISTRY.json. Closed sets are executable (R0.2/R0.3):
# every recognized value lives in REGISTRY.json, and every REGISTRY entry has a code
# counterpart. Unknown value at runtime => hard error, never a fallback.

from __future__ import annotations

import json
from pathlib import Path

from openstore.models import (
    CampaignState,
    LedgerEntryType,
    OrderState,
)

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / "REGISTRY.json").read_text())


def test_registry_declares_exhaustive_enums():
    assert REGISTRY["enums_are_exhaustive"] is True


def _values(enum_cls):
    return {str(member.value) for member in enum_cls}


def test_order_states_exhaustive():
    assert _values(OrderState) == set(REGISTRY["order_states"])


def test_campaign_states_exhaustive():
    assert _values(CampaignState) == set(REGISTRY["campaign_states"])


def test_ledger_entries_exhaustive():
    assert _values(LedgerEntryType) == set(REGISTRY["ledger_entries"])


def test_ledger_accounts_are_namespaced():
    """Ledger accounts in REGISTRY are namespaced by tier, not a flat enum."""
    assert list(REGISTRY["ledger_accounts"].keys()) == ["escrow", "economic"]
    assert set(REGISTRY["ledger_accounts"]["escrow"]) == {"customer_hold", "merchant_pending"}
    assert set(REGISTRY["ledger_accounts"]["economic"]) == {"merchant_revenue", "platform"}
