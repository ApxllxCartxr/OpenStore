# tests/stage14/test_order_tools.py
# DECISION-027: list_orders/cancel_order — closes FederatedBuyerBot's broken
# cancel path by giving a remote buyer process an identity-scoped, ownership-
# checked way to find and cancel its own orders (no direct DB access, no
# raw checkout_id ever known in advance).

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import openstore.core.database as _database_module
import pytest
from openstore.core.database import (
    get_or_create_checkout,
    get_session,
    init_database,
    update_checkout_state,
)
from openstore.core.ledger import create_reserve_entry
from openstore.models import Checkout, OrderState
from openstore.surfaces import mcp_server


@pytest.fixture()
def session(settings):
    """Override conftest's shared-engine `session` fixture: this file writes
    Checkout rows, and tests/test_checkout_flow.py asserts an exact
    checkouts-table row count against the same process-wide engine
    singleton (mirrors tests/stage11/test_phase3_money_chat.py's identical
    override)."""
    _database_module._engine = None
    init_database(settings)
    s = get_session(settings)
    yield s
    s.close()
    _database_module._engine = None


def _make_checkout(
    session,
    *,
    checkout_id: str,
    chat_user_id: str,
    chat_platform: str = "discord",
    state: OrderState = OrderState.HELD,
    amount_minor: int = 21000,
) -> Checkout:
    checkout, _ = get_or_create_checkout(
        session=session,
        checkout_id=checkout_id,
        trace_id=f"trace_{checkout_id}",
        client_id="oc_test",
        merchant_id="test-merchant",
        cart_hash=f"cart_{checkout_id}",
        cart_version=1,
        amount_minor=amount_minor,
        currency="INR",
        policy_id=None,
        policy_hash=None,
        aal_level=2,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
        idempotency_key=f"idem_{checkout_id}",
        cart_snapshot={"items": []},
    )
    create_reserve_entry(
        session=session,
        trace_id=checkout.trace_id,
        client_id=checkout.client_id,
        checkout_id=checkout.id,
        amount_minor=amount_minor,
        currency="INR",
    )
    update_checkout_state(
        session=session,
        checkout_id=checkout.id,
        new_state=OrderState.HELD,
        psp_payment_link_id=f"plink_{checkout_id}",
    )
    checkout.state = state
    checkout.chat_platform = chat_platform
    checkout.chat_user_id = chat_user_id
    session.add(checkout)
    session.commit()
    session.refresh(checkout)
    return checkout


class TestListOrders:
    def test_returns_only_the_calling_identitys_orders(self, settings, session):
        _make_checkout(session, checkout_id="chk_u1_a", chat_user_id="u1")
        _make_checkout(session, checkout_id="chk_u1_b", chat_user_id="u1")
        _make_checkout(session, checkout_id="chk_u2_a", chat_user_id="u2")

        result = mcp_server.list_orders(
            config=settings,
            session=session,
            chat_platform="discord",
            chat_user_id="u1",
            limit=5,
        )

        assert result.success is True
        ids = {o["checkout_id"] for o in result.data["orders"]}
        assert ids == {"chk_u1_a", "chk_u1_b"}

    def test_no_orders_returns_an_empty_list_not_an_error(self, settings, session):
        result = mcp_server.list_orders(
            config=settings,
            session=session,
            chat_platform="discord",
            chat_user_id="nobody",
        )
        assert result.success is True
        assert result.data["orders"] == []


class TestCancelOrder:
    def _cancel(self, settings, session, *, checkout_id, chat_user_id, scopes=None):
        return mcp_server.cancel_order(
            config=settings,
            session=session,
            trace_id="trace_cancel",
            client_id="discord:buyer-process",
            checkout_id=checkout_id,
            chat_platform="discord",
            chat_user_id=chat_user_id,
            token_scopes=scopes if scopes is not None else ["checkout:initiate"],
        )

    def test_cancels_a_held_checkout_the_caller_owns(self, settings, session, monkeypatch):
        checkout = _make_checkout(session, checkout_id="chk_cancel_ok", chat_user_id="u1")
        mock_client = MagicMock()
        mock_client.payment_link.cancel.return_value = {"id": "plink", "status": "cancelled"}
        monkeypatch.setattr("openstore.psp.razorpay_driver._get_client", lambda config: mock_client)

        result = self._cancel(settings, session, checkout_id=checkout.id, chat_user_id="u1")

        assert result.success is True
        session.refresh(checkout)
        assert checkout.state == OrderState.CANCELLED

    def test_rejects_a_checkout_owned_by_someone_else(self, settings, session):
        checkout = _make_checkout(session, checkout_id="chk_not_mine", chat_user_id="u1")

        result = self._cancel(settings, session, checkout_id=checkout.id, chat_user_id="u2")

        assert result.success is False
        assert result.error["reason_code"] == "checkout.not_owned"
        session.refresh(checkout)
        assert checkout.state == OrderState.HELD  # untouched

    def test_rejects_a_nonexistent_checkout(self, settings, session):
        result = self._cancel(settings, session, checkout_id="chk_nope", chat_user_id="u1")

        assert result.success is False
        assert result.error["reason_code"] == "checkout.not_found"

    def test_rejects_a_non_held_checkout(self, settings, session):
        checkout = _make_checkout(
            session, checkout_id="chk_already_paid", chat_user_id="u1", state=OrderState.PAID
        )

        result = self._cancel(settings, session, checkout_id=checkout.id, chat_user_id="u1")

        assert result.success is False
        assert result.error["reason_code"] == "psp.invalid_state"

    def test_requires_the_checkout_initiate_scope(self, settings, session):
        checkout = _make_checkout(session, checkout_id="chk_no_scope", chat_user_id="u1")

        result = self._cancel(
            settings, session, checkout_id=checkout.id, chat_user_id="u1", scopes=["catalog:read"]
        )

        assert result.success is False
        assert result.error["reason_code"] == "auth.insufficient_scope"
        session.refresh(checkout)
        assert checkout.state == OrderState.HELD  # untouched
