# tests/stage22/test_web_checkout.py
# Stage 22 (Q-042 / DECISION-042) — buyer-in-browser checkout over /web/*.
# No cookies/sessions: identity is web:<buyer_key>; authority is always a
# passkey tap through the existing CART-handoff ceremony. Golden WebAuthn
# fixtures (tests/make_webauthn_fixtures.py) drive the approval path.

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
from openstore.core.handoff import create_handoff
from openstore.core.webauthn_rp import ChallengeStore
from openstore.models import (
    Checkout,
    Handoff,
    HandoffKind,
    IntentPolicy,
    WebAuthnCredential,
)
from sqlmodel import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import make_webauthn_fixtures as wf  # noqa: E402, I001

BUYER_KEY = "webtestkey-alpha-01"
BUYER_HANDLE = f"web:{BUYER_KEY}"
_CRED_ID = b"\x0b" + b"\x03" * 31

CATALOG_YAML = """
items:
  - sku: GEL-VAN-500
    name: Madagascar Vanilla 500ml
    unit_minor: 21000
    tags: [vegan, dairy-free]
    description: Slow-churned, cashew-base vanilla.
  - sku: GEL-HAZ-500
    name: Roasted Hazelnut 500ml
    unit_minor: 23000
    tags: [vegan]
    description: Piedmont hazelnuts, dark roast.
"""


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture()
def settings(tmp_path) -> Settings:
    import openstore.surfaces.catalog as catalog_mod

    catalog_mod.CATALOG_CACHE = None
    catalog_mod._CATALOG_BY_PATH.clear()
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(CATALOG_YAML)
    return Settings(
        merchant=MerchantConfig(name="Test", currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxxxxxxx", key_secret="test"),
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
        catalog_path=str(catalog),
    )


@pytest.fixture()
def session_factory(settings: Settings):
    import openstore.core.database as _db

    prev = _db._engine
    _db._engine = None
    init_database(settings)
    try:
        yield lambda: get_session(settings)
    finally:
        from openstore.core.database import get_engine

        get_engine(settings).dispose()
        _db._engine = prev


@pytest.fixture()
def store() -> ChallengeStore:
    return ChallengeStore()


@pytest.fixture()
def client(settings: Settings, session_factory, store: ChallengeStore) -> TestClient:
    from openstore.surfaces.studio import policy_studio_router
    from openstore.surfaces.webcart import webcart_router

    app = FastAPI()
    app.include_router(
        policy_studio_router(settings, session_factory=session_factory, challenge_store=store)
    )
    app.include_router(webcart_router(settings, session_factory=session_factory))
    with TestClient(app) as c:
        yield c


def _enrol(session_factory, handle: str = BUYER_HANDLE) -> None:
    ec, _, _ = wf.keys()
    session = session_factory()
    try:
        session.add(
            WebAuthnCredential(
                credential_id=_b64u(_CRED_ID),
                user_handle=handle,
                public_key=wf.cose_ec2_p256_public(ec, -7),
                sign_count=3,
                is_active=True,
            )
        )
        session.commit()
    finally:
        session.close()


def _policy(session_factory, policy_id: str = "pol_web_base") -> IntentPolicy:
    now = int(datetime.now(UTC).timestamp())
    session = session_factory()
    try:
        p = IntentPolicy(
            id=policy_id,
            merchant_id="test",
            policy_hash="d" * 64,
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


def _approve(client, monkeypatch, approval_url: str, cart_id: str) -> dict:
    """Drive a golden passkey tap through cart_approve; returns the JSON."""
    import openstore.surfaces.studio as studio_module

    token = approval_url.split("token=")[1]
    page = client.get(approval_url)
    assert page.status_code == 200
    begin = _begin(page.text)

    seen: dict = {}

    def _fake_link(**kwargs):
        s = kwargs["session"]
        co = s.exec(select(Checkout).where(Checkout.id == kwargs["checkout_id"])).first()
        co.short_url = "https://pay.example/web"
        co.cancel_token = "tok_web"
        s.add(co)
        s.flush()
        seen["id"] = co.id
        return co

    monkeypatch.setattr(studio_module, "create_payment_link", _fake_link)

    async def _dm(config, user_id, message, embed=None):
        seen.setdefault("dms", []).append((user_id, message))

    monkeypatch.setattr(studio_module, "send_dm", _dm)

    assertion = _assert(begin["challenge"])
    res = client.post(
        f"/intent/cart/{cart_id}/approve",
        json={**assertion, "challenge": begin["challenge"], "token": token},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied"] is True
    return body


class TestWebCartValidation:
    def test_bad_buyer_key_422(self, client):
        r = client.post("/web/cart", json={"buyer_key": "x", "items": [{"sku": "GEL-VAN-500", "qty": 1}]})
        assert r.status_code == 422

    def test_unknown_sku_404_registered_code(self, client):
        r = client.post(
            "/web/cart",
            json={"buyer_key": BUYER_KEY, "items": [{"sku": "NOPE", "qty": 1}]},
        )
        assert r.status_code == 404
        assert r.json()["detail"]["reason_code"] == "catalog.sku_not_found"
        registry = json.loads(
            (Path(__file__).resolve().parents[2] / "REGISTRY.json").read_text()
        )
        assert "catalog.sku_not_found" in registry["reason_codes"]

    @pytest.mark.parametrize("qty", [0, -2, "2", 1.5, None])
    def test_bad_qty_422(self, client, qty):
        r = client.post(
            "/web/cart",
            json={"buyer_key": BUYER_KEY, "items": [{"sku": "GEL-VAN-500", "qty": qty}]},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason_code"] == "policy.qty_invalid"

    def test_empty_items_422(self, client):
        r = client.post("/web/cart", json={"buyer_key": BUYER_KEY, "items": []})
        assert r.status_code == 422

    def test_client_prices_ignored(self, client, session_factory):
        """A forged unit_minor in the request never reaches the cart payload."""
        _enrol(session_factory)
        _policy(session_factory)
        r = client.post(
            "/web/cart",
            json={
                "buyer_key": BUYER_KEY,
                "items": [{"sku": "GEL-VAN-500", "qty": 1, "unit_minor": 1}],
            },
        )
        assert r.json()["state"] == "approval"
        session = session_factory()
        try:
            handoffs = session.exec(select(Handoff)).all()
            payloads = [h.cart_payload for h in handoffs if h.cart_payload]
            assert payloads and payloads[0]["cart"][0]["unit_minor"] == 21000
        finally:
            session.close()


class TestWebCartMint:
    def test_no_policy_returns_signin(self, client, session_factory):
        r = client.post(
            "/web/cart", json={"buyer_key": BUYER_KEY, "items": [{"sku": "GEL-VAN-500", "qty": 1}]}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "signin"
        assert body["signin_url"].startswith("/intent/studio?token=")
        page = client.get(body["signin_url"])
        assert page.status_code == 200

    def test_policy_returns_approval_with_server_hash(self, client, session_factory):
        _enrol(session_factory)
        p = _policy(session_factory)
        r = client.post(
            "/web/cart", json={"buyer_key": BUYER_KEY, "items": [{"sku": "GEL-VAN-500", "qty": 2}]}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "approval"
        page = client.get(body["approval_url"])
        assert page.status_code == 200
        assert body["cart_id"] in page.text
        # The minted payload hash matches a server-side recompute (R0.8).
        session = session_factory()
        try:
            from openstore.models import Handoff

            h = session.exec(select(Handoff).where(Handoff.kind == HandoffKind.CART)).first()
            assert h is not None
            assert h.cart_payload["policy_id"] == p.id
            assert h.cart_payload["cart_hash"] == compute_cart_hash(h.cart_payload["cart"])
        finally:
            session.close()


class TestWebApprovalFlow:
    def test_full_approval_creates_web_checkout(self, client, session_factory, monkeypatch):
        _enrol(session_factory)
        _policy(session_factory)
        mint = client.post(
            "/web/cart", json={"buyer_key": BUYER_KEY, "items": [{"sku": "GEL-VAN-500", "qty": 1}]}
        ).json()
        body = _approve(client, monkeypatch, mint["approval_url"], mint["cart_id"])
        shop = body["shop_result"]
        assert shop["short_url"] == "https://pay.example/web"
        session = session_factory()
        try:
            co = session.exec(select(Checkout).where(Checkout.id == shop["checkout_id"])).first()
            assert co is not None
            assert co.chat_platform == "web"
            assert co.chat_user_id == BUYER_KEY
            assert co.aal_level >= 2
        finally:
            session.close()

    def test_status_flow(self, client, session_factory, monkeypatch):
        _enrol(session_factory)
        _policy(session_factory)
        mint = client.post(
            "/web/cart", json={"buyer_key": BUYER_KEY, "items": [{"sku": "GEL-VAN-500", "qty": 1}]}
        ).json()
        shop = _approve(client, monkeypatch, mint["approval_url"], mint["cart_id"])["shop_result"]
        cid = shop["checkout_id"]
        r = client.get(f"/web/order/{cid}?buyer_key={BUYER_KEY}")
        assert r.status_code == 200
        status = r.json()
        assert status["state"] == "awaiting_payment"
        assert status["short_url"] == "https://pay.example/web"
        assert status["evidence_url"] is None
        assert status["cancelable"] is True
        assert "cancel_token" not in status
        # Wrong key learns nothing beyond non-ownership.
        assert client.get(f"/web/order/{cid}?buyer_key=wrongkey0123456789").status_code == 403
        assert client.get("/web/order/chk_nope?buyer_key=" + BUYER_KEY).status_code == 404

    def test_cancel_flow(self, client, session_factory, monkeypatch):
        _enrol(session_factory)
        _policy(session_factory)
        mint = client.post(
            "/web/cart", json={"buyer_key": BUYER_KEY, "items": [{"sku": "GEL-VAN-500", "qty": 1}]}
        ).json()
        shop = _approve(client, monkeypatch, mint["approval_url"], mint["cart_id"])["shop_result"]
        cid = shop["checkout_id"]

        # Mock at the SDK boundary so the real cancel path (cancel_hold state
        # machine + ledger RELEASE) executes instead of a stubbed subset.
        import razorpay

        class _FakeLinks:
            def cancel(self, link_id):
                return {"status": "cancelled", "id": link_id}

        class _FakeClient:
            def __init__(self, auth=None):
                self.payment_link = _FakeLinks()

        monkeypatch.setattr(razorpay, "Client", _FakeClient)
        r = client.post(f"/web/order/{cid}/cancel?buyer_key={BUYER_KEY}")
        assert r.status_code == 200, r.text
        assert r.json()["cancelled"] is True
        status = client.get(f"/web/order/{cid}?buyer_key={BUYER_KEY}").json()
        assert status["state"] == "terminal"
        assert status["cancelable"] is False
        # Stranger's key cannot cancel.
        assert (
            client.post(f"/web/order/{cid}/cancel?buyer_key=wrongkey0123456789").status_code
            == 403
        )


class TestWebResumeGuard:
    def test_signing_does_not_resume_web_handoff(self, client, session_factory, monkeypatch):
        """Q-042(iv): _consume_and_resume must not mint a stray checkout for a
        web signing even with buyer_bot_enabled — the buyer returns to /chat."""
        import openstore.surfaces.studio as studio_module

        _enrol(session_factory)

        async def _never_resume(*args, **kwargs):
            raise AssertionError("resume_after_signing must not run for web")

        monkeypatch.setattr(studio_module, "resume_after_signing", _never_resume)
        dms: list = []

        async def _dm(config, user_id, message, embed=None):
            dms.append((user_id, message))

        monkeypatch.setattr(studio_module, "send_dm", _dm)

        session = session_factory()
        try:
            h = create_handoff(
                session,
                kind=HandoffKind.POLICY,
                merchant_id="test",
                chat_platform="web",
                chat_user_id="guardkey0123456789",
                chat_channel_id="web",
                request_text="guard",
            )
            session.commit()
            token = h.token
        finally:
            session.close()

        import asyncio

        # _consume_and_resume needs the app config; a stub carrying
        # buyer_bot_enabled=True proves the platform guard, not the flag.
        from types import SimpleNamespace

        config = SimpleNamespace(discord=SimpleNamespace(buyer_bot_enabled=True))
        outcome = asyncio.run(
            studio_module._consume_and_resume(config, session_factory, token, "pol_web_base")
        )
        assert outcome == {"resumed": False}
        session = session_factory()
        try:
            assert session.exec(select(Checkout)).all() == []
        finally:
            session.close()


class TestChatPageMarkers:
    def test_web_flow_markers(self):
        html = open(
            Path(__file__).resolve().parents[2]
            / "src"
            / "openstore"
            / "surfaces"
            / "static"
            / "chat.html",
            encoding="utf-8",
        ).read()
        for marker in (
            "chat-buy",
            "openstore_buyer_key",
            "/web/cart",
            "/web/order/",
            "#order=",
            "chat-checkout-status",
        ):
            assert marker in html, f"missing marker: {marker}"
        for forbidden in ("checkout_initiate", "client_secret", "password"):
            assert forbidden not in html.lower()
