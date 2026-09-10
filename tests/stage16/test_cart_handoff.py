# tests/stage16/test_cart_handoff.py
# S16 (Q-033): kind=CART handoff -> per-cart passkey tap bound to the cart hash
# -> checkout created with assertion_verified=True (AAL2). Replay onto a
# different cart fails; reject consumes without creating; second decision on a
# consumed handoff fails closed.

from __future__ import annotations

import base64
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openstore.agents.buyer_agent import compute_cart_hash
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
from openstore.core.handoff import create_handoff, resolve_handoff
from openstore.core.webauthn_rp import ChallengeStore
from openstore.models import Checkout, HandoffKind, IntentPolicy, WebAuthnCredential
from openstore.surfaces import studio as studio_module
from sqlmodel import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import make_webauthn_fixtures as wf  # noqa: E402, I001

BUYER_HANDLE = "discord:99"
_CRED_ID = b"\x0b" + b"\x03" * 31


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture()
def rp_settings() -> Settings:
    return Settings(
        merchant=MerchantConfig(name="Test"),
        razorpay=RazorpayConfig(key_id="k", key_secret="s"),
        discord=DiscordConfig(
            bot_token="t",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(
            rp_id="openstore.test", rp_name="x", origin="https://openstore.test"
        ),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def session_factory(rp_settings: Settings):
    import openstore.core.database as _db

    prev = _db._engine
    _db._engine = None
    init_database(rp_settings)
    try:
        yield lambda: get_session(rp_settings)
    finally:
        from openstore.core.database import get_engine

        get_engine(rp_settings).dispose()
        _db._engine = prev


@pytest.fixture()
def store() -> ChallengeStore:
    return ChallengeStore()


@pytest.fixture()
def client(rp_settings: Settings, session_factory, store: ChallengeStore) -> TestClient:
    from openstore.surfaces.studio import policy_studio_router

    app = FastAPI()
    app.include_router(
        policy_studio_router(rp_settings, session_factory=session_factory, challenge_store=store)
    )
    with TestClient(app) as c:
        yield c


def _enrol(session_factory) -> None:
    ec, _, _ = wf.keys()
    session = session_factory()
    try:
        session.add(
            WebAuthnCredential(
                credential_id=_b64u(_CRED_ID),
                user_handle=BUYER_HANDLE,
                public_key=wf.cose_ec2_p256_public(ec, -7),
                sign_count=3,
                is_active=True,
            )
        )
        session.commit()
    finally:
        session.close()


def _policy(session_factory, policy_id: str = "pol_cart_base") -> IntentPolicy:
    now = int(datetime.now(UTC).timestamp())
    session = session_factory()
    try:
        p = IntentPolicy(
            id=policy_id,
            merchant_id="test",
            policy_hash="c" * 64,
            max_spend_per_tx_minor=100_000,
            max_spend_total_minor=10_000_000,
            max_transactions=100,
            allowed_tags=[],
            blocked_skus=[],
            required_skus=[],
            not_before=now - 10,
            expires_at=now + 3600,
            webauthn_credential_id=_b64u(_CRED_ID),
            webauthn_sign_count=3,
            signed_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(p)
        session.commit()
        session.refresh(p)
        return p
    finally:
        session.close()


CART = [{"sku": "gelato", "qty": 2, "unit_minor": 500, "tags": []}]


def _cart_handoff(session_factory, policy_id: str) -> tuple[str, str, str]:
    cart_hash = compute_cart_hash(CART)
    cart_id = "cart_test123"
    session = session_factory()
    try:
        h = create_handoff(
            session,
            kind=HandoffKind.CART,
            merchant_id="test",
            chat_platform="discord",
            chat_user_id="99",
            chat_channel_id="1",
            request_text="two gelatos please",
            cart_payload={
                "cart_id": cart_id,
                "cart": CART,
                "cart_hash": cart_hash,
                "policy_id": policy_id,
            },
        )
        session.commit()
        return h.token, cart_id, cart_hash
    finally:
        session.close()


def _begin(html: str) -> dict:
    m = re.search(r"const ASSERTION_BEGIN = (\{.*?\});", html, re.DOTALL)
    assert m, "cart page did not embed ASSERTION_BEGIN"
    return json.loads(m.group(1))


def _assert(challenge_b64: str, sign_count: int = 4) -> dict:
    ec, _, _ = wf.keys()
    cd, auth, sig = wf.build_assertion(_CRED_ID, _b64d(challenge_b64), sign_count, ec, wf.sign_es256)
    return {
        "credential_id": _b64u(_CRED_ID),
        "client_data_json": _b64u(cd),
        "authenticator_data": _b64u(auth),
        "signature": _b64u(sig),
    }


class TestCartHandoff:
    def test_create_resolve_cart_kind(self, session_factory):
        _enrol(session_factory)
        p = _policy(session_factory)
        token, cart_id, cart_hash = _cart_handoff(session_factory, p.id)
        s = session_factory()
        try:
            h = resolve_handoff(s, token)
            assert h.kind == HandoffKind.CART
            assert h.cart_payload["cart_id"] == cart_id
            assert h.cart_payload["cart_hash"] == cart_hash
        finally:
            s.close()

    def test_page_renders_cart_binding(self, client, session_factory):
        _enrol(session_factory)
        p = _policy(session_factory)
        token, cart_id, cart_hash = _cart_handoff(session_factory, p.id)
        res = client.get(f"/intent/studio?token={token}")
        assert res.status_code == 200
        assert cart_id in res.text
        begin = _begin(res.text)
        assert begin["binding"] == {"mode": "cart", "cart_hash": cart_hash}

    def test_approve_creates_aal2_checkout(self, client, session_factory, monkeypatch):
        _enrol(session_factory)
        p = _policy(session_factory)
        token, cart_id, _ = _cart_handoff(session_factory, p.id)

        page = client.get(f"/intent/studio?token={token}")
        begin = _begin(page.text)
        assertion = _assert(begin["challenge"])

        seen: dict = {}

        def _fake_link(**kwargs):
            s = kwargs["session"]
            co = s.exec(select(Checkout).where(Checkout.id == kwargs["checkout_id"])).first()
            co.short_url = "https://pay.example/cart"
            co.cancel_token = "tok_cart"
            s.add(co)
            s.flush()
            seen["id"] = co.id
            return co

        monkeypatch.setattr(studio_module, "create_payment_link", _fake_link)

        dms: list = []

        async def _dm(config, user_id, message, embed=None):
            dms.append((user_id, message))

        monkeypatch.setattr(studio_module, "send_dm", _dm)

        res = client.post(
            f"/intent/cart/{cart_id}/approve",
            json={**assertion, "challenge": begin["challenge"], "token": token},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["applied"] is True
        # 2×500 = 1000 minor <= AAL3 threshold (10000): per-cart assertion
        # grades above the old chat ceiling (AAL1) — Q-033 closed.
        assert body["shop_result"]["aal_level"] == 3
        assert body["shop_result"]["checkout_id"] == seen["id"]
        assert dms and dms[0][0] == "99"

    def test_replay_onto_different_cart_fails(self, client, session_factory):
        """A cart-A assertion presented for cart-B's handoff must fail 401 —
        the binding pins cart_hash, not just any valid signature."""
        _enrol(session_factory)
        p = _policy(session_factory)
        token_a, _, _ = _cart_handoff(session_factory, p.id)

        other = [{"sku": "truffle", "qty": 1, "unit_minor": 99999, "tags": []}]
        other_hash = compute_cart_hash(other)
        s = session_factory()
        try:
            hb = create_handoff(
                s,
                kind=HandoffKind.CART,
                merchant_id="test",
                chat_platform="discord",
                chat_user_id="99",
                chat_channel_id="1",
                request_text="truffle",
                cart_payload={
                    "cart_id": "cart_other",
                    "cart": other,
                    "cart_hash": other_hash,
                    "policy_id": p.id,
                },
            )
            s.commit()
            token_b, cart_b = hb.token, "cart_other"
        finally:
            s.close()

        page_a = client.get(f"/intent/studio?token={token_a}")
        begin_a = _begin(page_a.text)
        assertion_a = _assert(begin_a["challenge"], sign_count=5)

        res = client.post(
            f"/intent/cart/{cart_b}/approve",
            json={**assertion_a, "challenge": begin_a["challenge"], "token": token_b},
        )
        assert res.status_code == 401
        assert res.json()["detail"]["reason_code"] == "assertion_required"

    def test_reject_consumes_without_checkout(self, client, session_factory, monkeypatch):
        _enrol(session_factory)
        p = _policy(session_factory)
        token, cart_id, _ = _cart_handoff(session_factory, p.id)

        dms: list = []

        async def _dm(config, user_id, message, embed=None):
            dms.append(message)

        monkeypatch.setattr(studio_module, "send_dm", _dm)

        res = client.post(f"/intent/cart/{cart_id}/reject", json={"token": token})
        assert res.status_code == 200
        assert res.json()["state"] == "REJECTED"
        assert dms

        s = session_factory()
        try:
            assert s.exec(select(Checkout)).all() == []
        finally:
            s.close()

    def test_cart_id_mismatch_and_missing_assertion(
        self, client, session_factory, monkeypatch
    ):
        _enrol(session_factory)
        p = _policy(session_factory)
        token, cart_id, _ = _cart_handoff(session_factory, p.id)

        res = client.post("/intent/cart/cart_nope/approve", json={"token": token})
        assert res.status_code == 404

        res = client.post(f"/intent/cart/{cart_id}/approve", json={"token": token})
        assert res.status_code == 422
        assert res.json()["detail"]["reason_code"] == "assertion_required"
