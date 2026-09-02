# tests/test_ledger_golden.py
# GOLDEN/ledger lifecycle vectors (INV-5a / Q-002).
# Each vector pins a full lifecycle; verify_ledger_balances MUST reproduce the
# pinned account_balances and boolean byte-for-byte.

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from openstore.core.ledger import (
    LedgerEntryType,
    create_capture_entry,
    create_refund_entry,
    create_release_entry,
    create_reserve_entry,
    verify_ledger_balances,
)
from openstore.models import Checkout, LedgerEntry, OrderState
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

GOLDEN_DIR = Path("GOLDEN/ledger")

_LIFECYCLES = {
    "reserve_capture": lambda s, ref: (
        create_reserve_entry(s, "t", "c", ref, 10000, "INR", "r"),
        create_capture_entry(s, "t", "c", ref, 10000, "INR", "c"),
    ),
    "reserve_release": lambda s, ref: (
        create_reserve_entry(s, "t", "c", ref, 10000, "INR", "r"),
        create_release_entry(s, "t", "c", ref, 10000, "INR", "rl"),
    ),
    "reserve_capture_refund": lambda s, ref: (
        create_reserve_entry(s, "t", "c", ref, 10000, "INR", "r"),
        create_capture_entry(s, "t", "c", ref, 10000, "INR", "c"),
        create_refund_entry(s, "t", "c", ref, 10000, "INR", "rf"),
    ),
}


def _canonical(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)


def _fresh_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _account_balances(session: Session, ref: str) -> dict[str, int]:
    bal: dict[str, int] = {}
    for e in session.exec(select(LedgerEntry).where(LedgerEntry.reference_id == ref)).all():
        bal.setdefault(e.account, 0)
        if e.entry_type in (LedgerEntryType.RESERVE, LedgerEntryType.CAPTURE):
            bal[e.account] += e.amount_minor
        else:
            bal[e.account] -= e.amount_minor
    return {k: bal[k] for k in sorted(bal)}


def _seed_checkout(session: Session, checkout_id: str) -> None:
    session.add(
        Checkout(
            id=checkout_id,
            trace_id="t",
            client_id="c",
            merchant_id="m",
            cart_hash="h",
            cart_version=1,
            amount_minor=10000,
            currency="INR",
            state=OrderState.CREATED,
            policy_id="pol_A",
            policy_hash="ph",
            aal_level=0,
            expires_at=datetime.now(UTC).replace(tzinfo=None),
            idempotency_key=f"ik_{checkout_id}",
            cart_snapshot={"items": []},
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    session.commit()


def test_vectors_exist():
    names = {"reserve_capture", "reserve_release", "reserve_capture_refund"}
    existing = {p.stem for p in GOLDEN_DIR.glob("*.json")}
    assert names.issubset(existing), f"missing golden vectors: {names - existing}"


@pytest.mark.parametrize(
    ("lifecycle", "expected_terminal"),
    [
        ("reserve_capture", OrderState.RELEASED),
        ("reserve_release", OrderState.CANCELLED),
        ("reserve_capture_refund", OrderState.REFUNDED),
    ],
)
def test_lifecycle_reproduces_vector(lifecycle, expected_terminal):
    path = GOLDEN_DIR / f"{lifecycle}.json"
    vector = json.loads(path.read_text())
    assert vector["lifecycle"] == lifecycle

    ref = vector["reference_id"]
    session = _fresh_session()
    _seed_checkout(session, ref)
    _LIFECYCLES[lifecycle](session, ref)

    checkout = session.exec(select(Checkout).where(Checkout.id == ref)).one()
    checkout.state = expected_terminal
    session.add(checkout)
    session.commit()

    actual_balances = _account_balances(session, ref)
    actual_verify = verify_ledger_balances(session, ref)

    assert _canonical(actual_balances) == _canonical(vector["account_balances"]), (
        f"{lifecycle}: account_balances diverged from golden vector"
    )
    assert actual_verify is vector["verify_ledger_balances"], (
        f"{lifecycle}: verify_ledger_balances diverged from golden vector"
    )
