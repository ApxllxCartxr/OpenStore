# tests/stage11/test_phase3_money_chat.py
# S11 Phase 3: money reaches chat — cancel-by-command, webhook -> DM push,
# hold-release loop notifications, checkout_initiate's state/expires_at/
# customer, and the callback_url public_base_url precedence.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from openstore.psp import razorpay_driver as driver
from openstore.psp import router as psp_router_module
from openstore.surfaces import mcp_server

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "GOLDEN" / "razorpay"


@pytest.fixture()
def session(settings):
    """Override conftest's shared-engine `session` fixture: this file writes
    Checkout rows (unlike the other tests/stage11/ files, which only touch
    Handoff), and tests/test_checkout_flow.py asserts an exact checkouts-table
    row count against the same process-wide engine singleton. Reset the
    engine before AND after so neither this file nor its neighbours in
    collection order see leaked rows (mirrors tests/stage05/test_webhook_replay.py's
    `app` fixture and tests/stage05/test_inv4_recovery.py's `psp_session` fixture)."""
    _database_module._engine = None
    init_database(settings)
    s = get_session(settings)
    yield s
    s.close()
    _database_module._engine = None


def _make_held_checkout(
    session,
    *,
    checkout_id: str,
    amount_minor: int = 21000,
    aal_level: int = 2,
    expires_at: datetime | None = None,
    chat_user_id: str | None = None,
    chat_platform: str | None = "discord",
    chat_channel_id: str | None = "chan_1",
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
        aal_level=aal_level,
        expires_at=expires_at or (datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15)),
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
    checkout.cancel_token = f"cancel_{checkout_id}"
    checkout.chat_platform = chat_platform
    checkout.chat_user_id = chat_user_id
    checkout.chat_channel_id = chat_channel_id
    session.add(checkout)
    session.commit()
    session.refresh(checkout)
    return checkout


# ---------------------------------------------------------------------------
# cancel_checkout_by_id — shared cancel logic (bot command + HTTP route)
# ---------------------------------------------------------------------------


class TestCancelCheckoutById:
    def test_cancels_held_checkout(self, settings, session):
        checkout = _make_held_checkout(session, checkout_id="chk_cancel_ok", chat_user_id="u1")

        mock_client = MagicMock()
        mock_client.payment_link.cancel.return_value = {
            "id": "plink_chk_cancel_ok",
            "status": "cancelled",
        }

        result = driver.cancel_checkout_by_id(
            config=settings,
            session=session,
            trace_id="trace_cancel",
            client_id="discord:u1",
            checkout=checkout,
            mock_razorpay=mock_client,
        )
        session.commit()

        assert result["status"] == "RELEASE"
        session.refresh(checkout)
        assert checkout.state == OrderState.CANCELLED

    def test_rejects_non_held_checkout(self, settings, session):
        checkout = _make_held_checkout(
            session, checkout_id="chk_cancel_bad_state", chat_user_id="u1"
        )
        checkout.state = OrderState.PAID
        session.add(checkout)
        session.commit()

        with pytest.raises(driver.RazorpayError) as exc:
            driver.cancel_checkout_by_id(
                config=settings,
                session=session,
                trace_id="trace_cancel",
                client_id="discord:u1",
                checkout=checkout,
            )
        assert exc.value.error_code == "psp.invalid_state"


class TestBuyerBotCancelCommand:
    async def test_cancel_own_checkout_succeeds(self, settings, session):
        from openstore.agents.buyer_agent import BuyerAgent, BuyerBot

        checkout = _make_held_checkout(
            session, checkout_id="chk_bot_cancel_ok", chat_user_id="555001"
        )

        mock_client = MagicMock()
        mock_client.payment_link.cancel.return_value = {
            "id": checkout.psp_payment_link_id,
            "status": "cancelled",
        }
        original_get_client = driver._get_client
        driver._get_client = lambda _cfg: mock_client  # type: ignore[assignment]
        try:
            bot = BuyerBot(settings, BuyerAgent(settings, None))

            class _Author:
                id = 555001

            class _Channel:
                def __init__(self):
                    self.sent: list[str] = []

                async def send(self, content):
                    self.sent.append(content)

            class _Message:
                author = _Author()
                channel = _Channel()
                id = 1

            message = _Message()
            await bot._handle_cancel(message, checkout.id)

            assert message.channel.sent == ["Cancelled."]
        finally:
            driver._get_client = original_get_client

    async def test_cancel_rejects_other_users_checkout(self, settings, session):
        from openstore.agents.buyer_agent import BuyerAgent, BuyerBot

        checkout = _make_held_checkout(
            session, checkout_id="chk_bot_cancel_other", chat_user_id="555001"
        )

        bot = BuyerBot(settings, BuyerAgent(settings, None))

        class _Author:
            id = 999999  # not the checkout's owner

        class _Channel:
            def __init__(self):
                self.sent: list[str] = []

            async def send(self, content):
                self.sent.append(content)

        class _Message:
            author = _Author()
            channel = _Channel()
            id = 2

        message = _Message()
        await bot._handle_cancel(message, checkout.id)

        assert message.channel.sent == ["Not your checkout."]
        session.refresh(checkout)
        assert checkout.state == OrderState.HELD  # untouched


# ---------------------------------------------------------------------------
# Webhook -> DM push
# ---------------------------------------------------------------------------


class TestWebhookChatPush:
    def test_payment_link_paid_dms_buyer_and_pushes_money_trace(
        self, settings, session, monkeypatch
    ):
        checkout = _make_held_checkout(
            session, checkout_id="chk_webhook_paid", amount_minor=15000, chat_user_id="d42"
        )

        payload = json.loads((GOLDEN_DIR / "payment_link_paid.json").read_text())
        entity = payload["payload"]["payment_link"]["entity"]
        entity["reference_id"] = checkout.id
        entity["notes"]["checkout_id"] = checkout.id
        entity["amount"] = checkout.amount_minor
        body = json.dumps(payload).encode()

        event = driver.persist_raw_webhook_event(
            session=session,
            raw_body=body,
            signature="test",
            x_event_id="evt_paid_push",
            trace_id="trace_paid_push",
            client_id="razorpay",
        )
        session.commit()

        dm_calls: list[tuple[str, str]] = []

        async def _fake_send_dm(config, user_id, message):
            dm_calls.append((user_id, message))

        trace_calls: list[tuple] = []

        async def _fake_money_trace(self, trace_id, action, amount_minor, currency="INR"):
            trace_calls.append((trace_id, action, amount_minor, currency))

        import openstore.notifier as notifier_module

        monkeypatch.setattr(notifier_module, "send_dm", _fake_send_dm)
        monkeypatch.setattr(notifier_module.DiscordNotifier, "money_trace", _fake_money_trace)

        psp_router_module._process_event_in_worker(event.psp_event_id, settings)

        # S11 Phase 4: a RELEASED chat checkout also gets an evidence bundle
        # + a second DM with the receipt link (plan items #20-22).
        assert dm_calls == [
            ("d42", "Paid! ₹150.00 — order released."),
            ("d42", "Evidence: http://localhost/orders/chk_webhook_paid/evidence/view"),
        ]
        assert trace_calls == [(checkout.trace_id, "PAID", 15000, "INR")]

        session.refresh(checkout)
        assert checkout.state == OrderState.RELEASED
        assert checkout.poai_bundle is not None

    def test_payment_link_cancelled_dms_buyer(self, settings, session, monkeypatch):
        checkout = _make_held_checkout(
            session, checkout_id="chk_webhook_cancelled", chat_user_id="d43"
        )

        payload = json.loads((GOLDEN_DIR / "payment_link_cancelled.json").read_text())
        entity = payload["payload"]["payment_link"]["entity"]
        entity["reference_id"] = checkout.id
        entity["notes"]["checkout_id"] = checkout.id
        body = json.dumps(payload).encode()

        event = driver.persist_raw_webhook_event(
            session=session,
            raw_body=body,
            signature="test",
            x_event_id="evt_cancelled_push",
            trace_id="trace_cancelled_push",
            client_id="razorpay",
        )
        session.commit()

        dm_calls: list[tuple[str, str]] = []

        async def _fake_send_dm(config, user_id, message):
            dm_calls.append((user_id, message))

        async def _fake_money_trace(self, trace_id, action, amount_minor, currency="INR"):
            return None

        import openstore.notifier as notifier_module

        monkeypatch.setattr(notifier_module, "send_dm", _fake_send_dm)
        monkeypatch.setattr(notifier_module.DiscordNotifier, "money_trace", _fake_money_trace)

        psp_router_module._process_event_in_worker(event.psp_event_id, settings)

        assert dm_calls == [("d43", "Hold cancelled.")]

    def test_no_chat_user_id_sends_no_dm(self, settings, session, monkeypatch):
        """A non-chat-originated checkout (chat_user_id unset) must not trigger a DM."""
        checkout = _make_held_checkout(
            session, checkout_id="chk_webhook_no_chat", chat_user_id=None
        )

        payload = json.loads((GOLDEN_DIR / "payment_link_paid.json").read_text())
        entity = payload["payload"]["payment_link"]["entity"]
        entity["reference_id"] = checkout.id
        entity["notes"]["checkout_id"] = checkout.id
        entity["amount"] = checkout.amount_minor
        body = json.dumps(payload).encode()

        event = driver.persist_raw_webhook_event(
            session=session,
            raw_body=body,
            signature="test",
            x_event_id="evt_no_chat_push",
            trace_id="trace_no_chat_push",
            client_id="razorpay",
        )
        session.commit()

        dm_calls: list[tuple[str, str]] = []

        async def _fake_send_dm(config, user_id, message):
            dm_calls.append((user_id, message))

        import openstore.notifier as notifier_module

        monkeypatch.setattr(notifier_module, "send_dm", _fake_send_dm)

        psp_router_module._process_event_in_worker(event.psp_event_id, settings)

        assert dm_calls == []


# ---------------------------------------------------------------------------
# Hold-release loop notifications
# ---------------------------------------------------------------------------


class TestHoldReleaseNotifications:
    def test_released_hold_with_chat_user_id_is_reported(self, settings, session):
        expired = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
        checkout = _make_held_checkout(
            session,
            checkout_id="chk_hold_release",
            aal_level=1,  # AAL1/2 -> cancel_hold path
            expires_at=expired,
            chat_user_id="d99",
        )

        released = driver.hold_release_worker_tick_with_notifications(settings, session)
        session.commit()

        assert released == [
            {"checkout_id": checkout.id, "chat_user_id": "d99", "state": "CANCELLED"}
        ]

    def test_released_hold_without_chat_user_id_is_not_reported(self, settings, session):
        expired = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
        _make_held_checkout(
            session,
            checkout_id="chk_hold_release_no_chat",
            aal_level=1,
            expires_at=expired,
            chat_user_id=None,
        )

        released = driver.hold_release_worker_tick_with_notifications(settings, session)
        session.commit()

        assert released == []


# ---------------------------------------------------------------------------
# checkout_initiate: state, expires_at, customer, chat identity stamping
# ---------------------------------------------------------------------------


class TestCheckoutInitiatePhase3:
    def _make_created_checkout(self, session, checkout_id: str) -> Checkout:
        checkout, _ = get_or_create_checkout(
            session=session,
            checkout_id=checkout_id,
            trace_id=f"trace_{checkout_id}",
            client_id="oc_test",
            merchant_id="test-merchant",
            cart_hash="cart_x",
            cart_version=1,
            amount_minor=5000,
            currency="INR",
            policy_id=None,
            policy_hash=None,
            aal_level=1,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
            idempotency_key=f"idem_{checkout_id}",
            cart_snapshot={"items": []},
        )
        update_checkout_state(session=session, checkout_id=checkout.id, new_state=OrderState.HELD)
        session.commit()
        session.refresh(checkout)
        return checkout

    def test_state_expires_at_and_customer_with_chat_identity(self, settings, session, monkeypatch):
        checkout = self._make_created_checkout(session, "chk_initiate_chat")
        captured: dict = {}

        def _fake_create_payment_link(**kwargs):
            captured.update(kwargs)
            kwargs["session"].refresh(checkout)
            return checkout

        monkeypatch.setattr(driver, "create_payment_link", _fake_create_payment_link)

        result = mcp_server.checkout_initiate(
            config=settings,
            session=session,
            client_id="buyer-agent",
            trace_id="trace_initiate",
            checkout_id=checkout.id,
            token_scopes=["checkout:initiate"],
            chat_platform="discord",
            chat_user_id="d100",
            chat_channel_id="c100",
        )

        assert result.success
        assert result.data["state"] == "HELD"
        assert result.data["expires_at"] == checkout.expires_at.isoformat()
        assert captured["customer"] == {"name": "Discord user d100"}

        session.refresh(checkout)
        assert checkout.chat_platform == "discord"
        assert checkout.chat_user_id == "d100"
        assert checkout.chat_channel_id == "c100"

    def test_no_customer_without_chat_identity(self, settings, session, monkeypatch):
        checkout = self._make_created_checkout(session, "chk_initiate_no_chat")
        captured: dict = {}

        def _fake_create_payment_link(**kwargs):
            captured.update(kwargs)
            return checkout

        monkeypatch.setattr(driver, "create_payment_link", _fake_create_payment_link)

        result = mcp_server.checkout_initiate(
            config=settings,
            session=session,
            client_id="buyer-agent",
            trace_id="trace_initiate",
            checkout_id=checkout.id,
            token_scopes=["checkout:initiate"],
        )

        assert result.success
        assert captured["customer"] is None
        assert checkout.chat_user_id is None


# ---------------------------------------------------------------------------
# callback_url prefers public_base_url
# ---------------------------------------------------------------------------


class TestCallbackUrlPrecedence:
    def test_callback_url_uses_public_base_url_when_set(self, settings, session):
        settings.public_base_url = "https://shop.example.com"
        checkout, _ = get_or_create_checkout(
            session=session,
            checkout_id="chk_callback_pub",
            trace_id="trace_callback_pub",
            client_id="oc_test",
            merchant_id="test-merchant",
            cart_hash="cart_x",
            cart_version=1,
            amount_minor=5000,
            currency="INR",
            policy_id=None,
            policy_hash=None,
            aal_level=1,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
            idempotency_key="idem_callback_pub",
            cart_snapshot={"items": []},
        )
        update_checkout_state(session=session, checkout_id=checkout.id, new_state=OrderState.HELD)
        session.commit()

        mock_client = MagicMock()
        captured_request: dict = {}

        def _fake_create(link_request):
            captured_request.update(link_request)
            return {
                "id": "plink_callback_pub",
                "reference_id": checkout.id,
                "short_url": "https://rzp.io/i/xyz",
            }

        mock_client.payment_link.create = _fake_create

        driver.create_payment_link(
            config=settings,
            session=session,
            trace_id="trace_callback_pub",
            client_id="oc_test",
            checkout_id=checkout.id,
            amount_minor=5000,
            mock_razorpay=mock_client,
        )

        # callback_url is the post-payment browser redirect, not the webhook
        # receiver — it must be a GET-able route (the storefront root), not
        # /webhooks/razorpay (POST-only, always 405s a real redirect).
        assert captured_request["callback_url"] == "https://shop.example.com/"
