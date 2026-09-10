# tests/stage16/test_reconcile.py
# S16 (Q-032): pre-release PSP reconcile + set_order_message ownership.

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
)
from openstore.core.database import get_session, init_database
from openstore.models import Checkout, OrderState
from openstore.psp import razorpay_driver as driver
from openstore.surfaces import mcp_server


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test"),
        razorpay=RazorpayConfig(key_id="rzp_test_x", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="x", origin="http://localhost"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def session(settings):
    import openstore.core.database as _db

    prev = _db._engine
    _db._engine = None
    init_database(settings)
    s = get_session(settings)
    try:
        yield s
    finally:
        s.close()
        try:
            from openstore.core.database import get_engine

            get_engine(settings).dispose()
        except Exception:
            pass
        _db._engine = prev


def _held(session, checkout_id: str, link_id: str = "plink_test") -> Checkout:
    now = datetime.now(UTC).replace(tzinfo=None)
    co = Checkout(
        id=checkout_id,
        trace_id="t",
        client_id="c",
        merchant_id="test",
        cart_hash="h",
        cart_version=1,
        amount_minor=500,
        currency="INR",
        state=OrderState.HELD,
        expires_at=now + timedelta(seconds=60),
        idempotency_key=f"idem-{checkout_id}",
        psp_payment_link_id=link_id,
        cart_snapshot={},
        created_at=now - timedelta(seconds=60),
        updated_at=now - timedelta(seconds=60),
    )
    session.add(co)
    session.commit()
    session.refresh(co)
    return co


class TestPreReleaseReconcile:
    def test_paid_link_captured_before_release(self, settings, session):
        _held(session, "chk_recon_paid", "plink_paid")

        class _Mock:
            class payment_link:
                @staticmethod
                def fetch(link_id):
                    assert link_id == "plink_paid"
                    return {
                        "id": "plink_paid",
                        "reference_id": "chk_recon_paid",
                        "amount": 500,
                        "status": "paid",
                    }

        out = driver.reconcile_held_before_release(
            settings, session, warn_window_seconds=300, mock_razorpay=_Mock()
        )
        assert out == {"checked": 1, "reconciled": 1}
        session.refresh(session.exec(
            __import__("sqlmodel").select(Checkout).where(Checkout.id == "chk_recon_paid")
        ).first())
        co = session.exec(
            __import__("sqlmodel").select(Checkout).where(Checkout.id == "chk_recon_paid")
        ).first()
        assert co is not None and co.state == OrderState.RELEASED

    def test_psp_outage_never_blocks(self, settings, session):
        _held(session, "chk_recon_down", "plink_down")

        class _Boom:
            class payment_link:
                @staticmethod
                def fetch(link_id):
                    raise RuntimeError("psp down")

        out = driver.reconcile_held_before_release(
            settings, session, warn_window_seconds=300, mock_razorpay=_Boom()
        )
        assert out == {"checked": 1, "reconciled": 0}
        co = session.exec(
            __import__("sqlmodel").select(Checkout).where(Checkout.id == "chk_recon_down")
        ).first()
        assert co is not None and co.state == OrderState.HELD

    def test_unpaid_link_left_alone(self, settings, session):
        _held(session, "chk_recon_unpaid", "plink_unpaid")

        class _Mock:
            class payment_link:
                @staticmethod
                def fetch(link_id):
                    return {"id": link_id, "status": "created"}

        out = driver.reconcile_held_before_release(
            settings, session, warn_window_seconds=300, mock_razorpay=_Mock()
        )
        assert out == {"checked": 1, "reconciled": 0}


class TestSetOrderMessage:
    def test_stamp_and_ownership(self, settings, session):
        co = _held(session, "chk_msg_1", "plink_1")
        co.chat_platform = "discord"
        co.chat_user_id = "99"
        session.add(co)
        session.commit()

        ok = mcp_server.set_order_message(
            config=settings,
            session=session,
            checkout_id="chk_msg_1",
            chat_platform="discord",
            chat_user_id="99",
            discord_message_id="12345",
            token_scopes=["checkout:initiate"],
        )
        assert ok.success is True
        session.refresh(co)
        assert co.discord_message_id == "12345"

        # first writer wins
        ok2 = mcp_server.set_order_message(
            config=settings,
            session=session,
            checkout_id="chk_msg_1",
            chat_platform="discord",
            chat_user_id="99",
            discord_message_id="99999",
            token_scopes=["checkout:initiate"],
        )
        assert ok2.success is True
        session.refresh(co)
        assert co.discord_message_id == "12345"

        # wrong identity rejected
        bad = mcp_server.set_order_message(
            config=settings,
            session=session,
            checkout_id="chk_msg_1",
            chat_platform="discord",
            chat_user_id="other",
            discord_message_id="1",
            token_scopes=["checkout:initiate"],
        )
        assert bad.success is False
        assert bad.error["reason_code"] == "checkout.not_owned"

        # missing checkout
        missing = mcp_server.set_order_message(
            config=settings,
            session=session,
            checkout_id="chk_nope",
            chat_platform="discord",
            chat_user_id="99",
            discord_message_id="1",
            token_scopes=["checkout:initiate"],
        )
        assert missing.success is False
        assert missing.error["reason_code"] == "checkout.not_found"
