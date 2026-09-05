# tests/stage11/test_phase4_amendment.py
# S11 Phase 4: draft_amendment -> Handoff(kind=AMENDMENT) -> one-tap approval
# (real WebAuthn assertion, mirroring tests/stage03/test_policy_studio.py's
# crypto convention) -> apply the one-time relief -> recompile -> checkout
# created. Reject path consumes without applying. Second decision on an
# already-consumed handoff raises authority.handoff_consumed.

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
from openstore.agents.merchant_agent import MerchantAgent
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
_CRED_ID = b"\x0a" + b"\x02" * 31


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


def _enrol_buyer_credential(session_factory) -> None:
    """Insert a real credential (public key material from wf.keys()) under
    the buyer's user_handle, without going through the operator-gated
    /register endpoint (that ceremony is merchant-operator-scoped; a buyer's
    credential in this system rides the same webauthn_credentials table via
    a discord:<id> user_handle per the plan's identity design)."""
    ec, _, _ = wf.keys()
    session = session_factory()
    try:
        cred = WebAuthnCredential(
            credential_id=_b64u(_CRED_ID),
            user_handle=BUYER_HANDLE,
            public_key=wf.cose_ec2_p256_public(ec, -7),
            sign_count=3,
            is_active=True,
        )
        session.add(cred)
        session.commit()
    finally:
        session.close()


def _make_base_policy(
    session_factory, *, policy_hash: str, blocked_skus, max_spend_per_tx_minor: int
) -> IntentPolicy:
    now = int(datetime.now(UTC).timestamp())
    session = session_factory()
    try:
        policy = IntentPolicy(
            id="pol_amend_base",
            merchant_id="gelateria",
            policy_hash=policy_hash,
            max_spend_per_tx_minor=max_spend_per_tx_minor,
            max_spend_total_minor=10_000_000,
            max_transactions=100,
            allowed_tags=[],
            blocked_skus=blocked_skus,
            required_skus=[],
            not_before=now - 10,
            expires_at=now + 3600,
            webauthn_credential_id=_b64u(_CRED_ID),
            webauthn_sign_count=3,
            signed_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(policy)
        session.commit()
        session.refresh(policy)
        return policy
    finally:
        session.close()


def _make_amendment_handoff(
    session_factory, *, policy_hash: str, cart: list[dict], reason_code: str
):
    draft = MerchantAgent.__new__(MerchantAgent)  # config unused by draft_amendment
    draft.config = None
    drafted = draft.draft_amendment(policy_hash, reason_code, cart, "trace_amend_test")

    session = session_factory()
    try:
        handoff = create_handoff(
            session,
            kind=HandoffKind.AMENDMENT,
            merchant_id="gelateria",
            chat_platform="discord",
            chat_user_id="99",
            chat_channel_id="1",
            request_text="banana split please",
            amendment_draft={"draft": drafted, "cart": cart},
        )
        session.commit()
        token = handoff.token
    finally:
        session.close()
    return token, drafted


def _extract_assertion_begin(html: str) -> dict:
    match = re.search(r"const ASSERTION_BEGIN = (\{.*?\});", html, re.DOTALL)
    assert match, "amendment page did not embed ASSERTION_BEGIN"
    return json.loads(match.group(1))


def _extract_amendment_id(html: str) -> str:
    match = re.search(r'const AMENDMENT_ID = "(.*?)";', html)
    assert match, "amendment page did not embed AMENDMENT_ID"
    return match.group(1)


def _build_assertion(challenge_b64: str, sign_count: int = 4) -> dict:
    ec, _, _ = wf.keys()
    cd, auth, sig = wf.build_assertion(
        _CRED_ID, _b64d(challenge_b64), sign_count, ec, wf.sign_es256
    )
    return {
        "credential_id": _b64u(_CRED_ID),
        "client_data_json": _b64u(cd),
        "authenticator_data": _b64u(auth),
        "signature": _b64u(sig),
    }


class TestAmendmentApproval:
    def test_draft_creates_amendment_handoff(self, session_factory):
        cart = [{"sku": "banana", "qty": 1, "unit_minor": 500, "tags": []}]
        token, drafted = _make_amendment_handoff(
            session_factory, policy_hash="h" * 64, cart=cart, reason_code="policy.sku_blocked"
        )
        session = session_factory()
        try:
            handoff = resolve_handoff(session, token)
            assert handoff.kind == HandoffKind.AMENDMENT
            assert handoff.amendment_draft["draft"]["amendment_id"] == drafted["amendment_id"]
            assert handoff.amendment_draft["cart"] == cart
        finally:
            session.close()

    def test_studio_page_renders_amendment_delta(self, client: TestClient, session_factory):
        _enrol_buyer_credential(session_factory)
        _make_base_policy(
            session_factory,
            policy_hash="p" * 64,
            blocked_skus=["banana"],
            max_spend_per_tx_minor=100,
        )
        cart = [{"sku": "banana", "qty": 1, "unit_minor": 500, "tags": []}]
        token, drafted = _make_amendment_handoff(
            session_factory, policy_hash="p" * 64, cart=cart, reason_code="policy.sku_blocked"
        )

        res = client.get(f"/intent/studio?token={token}")
        assert res.status_code == 200
        assert drafted["amendment_id"] in res.text
        begin = _extract_assertion_begin(res.text)
        assert begin["challenge"]

    def test_approve_applies_relief_and_creates_checkout(
        self, client: TestClient, session_factory, monkeypatch
    ):
        _enrol_buyer_credential(session_factory)
        _make_base_policy(
            session_factory,
            policy_hash="q" * 64,
            blocked_skus=["banana"],
            max_spend_per_tx_minor=100,
        )
        cart = [{"sku": "banana", "qty": 1, "unit_minor": 500, "tags": []}]
        token, drafted = _make_amendment_handoff(
            session_factory, policy_hash="q" * 64, cart=cart, reason_code="policy.sku_blocked"
        )
        amendment_id = drafted["amendment_id"]

        page = client.get(f"/intent/studio?token={token}")
        begin = _extract_assertion_begin(page.text)
        assertion = _build_assertion(begin["challenge"])

        created_checkout = {}

        def _fake_create_payment_link(**kwargs):
            session = kwargs["session"]
            checkout = session.exec(
                select(Checkout).where(Checkout.id == kwargs["checkout_id"])
            ).first()
            checkout.short_url = "https://pay.example/amend"
            checkout.cancel_token = "tok_amend"
            session.add(checkout)
            session.flush()
            created_checkout["checkout"] = checkout
            return checkout

        monkeypatch.setattr(studio_module, "create_payment_link", _fake_create_payment_link)

        dm_calls: list[tuple[str, str]] = []

        async def _fake_send_dm(config, user_id, message, embed=None):
            dm_calls.append((user_id, message))

        monkeypatch.setattr(studio_module, "send_dm", _fake_send_dm)

        res = client.post(
            f"/intent/amendment/{amendment_id}/approve",
            json={**assertion, "challenge": begin["challenge"], "token": token},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["applied"] is True
        assert body["shop_result"]["allowed"] is True
        assert body["shop_result"]["checkout_id"] == created_checkout["checkout"].id

        # blocked_skus relief applied — the same cart that was blocked now compiles
        assert created_checkout["checkout"].amount_minor == 500
        assert any("Amendment approved" in m for _, m in dm_calls)

        # single-use: a second approve on the same (now-consumed) handoff fails loud
        res2 = client.post(
            f"/intent/amendment/{amendment_id}/approve",
            json={**assertion, "challenge": begin["challenge"], "token": token},
        )
        assert res2.status_code == 409
        assert res2.json()["detail"]["reason_code"] == "authority.handoff_consumed"

    def test_approve_still_denied_reports_reason_and_consumes(
        self, client: TestClient, session_factory, monkeypatch
    ):
        """The amendment's delta only relieves blocked_skus / bumps the per-tx
        cap — it does not touch every possible violation (Q-021). A cart still
        denied after relief is reported honestly, not silently retried."""
        _enrol_buyer_credential(session_factory)
        _make_base_policy(
            session_factory, policy_hash="r" * 64, blocked_skus=[], max_spend_per_tx_minor=100
        )
        # tag_violation is untouched by the amendment's delta fields.
        cart = [{"sku": "dairy-thing", "qty": 1, "unit_minor": 50, "tags": ["dairy"]}]

        session = session_factory()
        try:
            pol = session.exec(
                select(IntentPolicy).where(IntentPolicy.policy_hash == "r" * 64)
            ).first()
            pol.allowed_tags = ["vegan"]
            pol.tag_mode = "all"
            session.add(pol)
            session.commit()
        finally:
            session.close()

        token, drafted = _make_amendment_handoff(
            session_factory, policy_hash="r" * 64, cart=cart, reason_code="policy.tag_violation"
        )
        amendment_id = drafted["amendment_id"]

        page = client.get(f"/intent/studio?token={token}")
        begin = _extract_assertion_begin(page.text)
        assertion = _build_assertion(begin["challenge"])

        dm_calls: list[tuple[str, str]] = []

        async def _fake_send_dm(config, user_id, message, embed=None):
            dm_calls.append((user_id, message))

        monkeypatch.setattr(studio_module, "send_dm", _fake_send_dm)

        res = client.post(
            f"/intent/amendment/{amendment_id}/approve",
            json={**assertion, "challenge": begin["challenge"], "token": token},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["applied"] is False
        assert body["reason_code"] == "policy.tag_violation"
        # The DM says what went wrong in plain language; the raw closed-set
        # reason_code stays in the JSON response and the trace, never in chat.
        assert any("still doesn't fit" in m for _, m in dm_calls)
        assert not any("policy.tag_violation" in m for _, m in dm_calls)

        # consumed even though not applied — a decision was rendered
        res2 = client.post(
            f"/intent/amendment/{amendment_id}/reject",
            json={"token": token},
        )
        assert res2.status_code == 409

    def test_reject_consumes_without_applying(
        self, client: TestClient, session_factory, monkeypatch
    ):
        _enrol_buyer_credential(session_factory)
        _make_base_policy(
            session_factory,
            policy_hash="s" * 64,
            blocked_skus=["banana"],
            max_spend_per_tx_minor=100,
        )
        cart = [{"sku": "banana", "qty": 1, "unit_minor": 500, "tags": []}]
        token, drafted = _make_amendment_handoff(
            session_factory, policy_hash="s" * 64, cart=cart, reason_code="policy.sku_blocked"
        )
        amendment_id = drafted["amendment_id"]

        dm_calls: list[tuple[str, str]] = []

        async def _fake_send_dm(config, user_id, message, embed=None):
            dm_calls.append((user_id, message))

        monkeypatch.setattr(studio_module, "send_dm", _fake_send_dm)

        res = client.post(f"/intent/amendment/{amendment_id}/reject", json={"token": token})
        assert res.status_code == 200
        assert res.json() == {"applied": False, "state": "REJECTED"}
        assert dm_calls == [("99", "Amendment rejected, order not placed.")]

        session = session_factory()
        try:
            assert session.exec(select(Checkout)).first() is None
        finally:
            session.close()

        # second reject on the now-consumed handoff fails loud
        res2 = client.post(f"/intent/amendment/{amendment_id}/reject", json={"token": token})
        assert res2.status_code == 409
        assert res2.json()["detail"]["reason_code"] == "authority.handoff_consumed"

    def test_approve_without_assertion_fields_is_rejected(
        self, client: TestClient, session_factory
    ):
        _enrol_buyer_credential(session_factory)
        _make_base_policy(
            session_factory,
            policy_hash="u" * 64,
            blocked_skus=["banana"],
            max_spend_per_tx_minor=100,
        )
        cart = [{"sku": "banana", "qty": 1, "unit_minor": 500, "tags": []}]
        token, drafted = _make_amendment_handoff(
            session_factory, policy_hash="u" * 64, cart=cart, reason_code="policy.sku_blocked"
        )
        res = client.post(
            f"/intent/amendment/{drafted['amendment_id']}/approve",
            json={"token": token},
        )
        assert res.status_code == 422
        assert res.json()["detail"]["reason_code"] == "assertion_required"
