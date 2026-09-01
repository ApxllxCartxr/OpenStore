# tests/stage03/test_policy_studio.py
# S3.5: Policy Studio surface — the APIRouter from surfaces/studio.py exercised
# end-to-end through TestClient, plus the signing-gate behaviors (aggregate cap,
# LegacyPolicyError). Assertions are forged with real crypto against the committed
# GOLDEN/webauthn keys and the per-call random challenges the server issues
# (R0.7: real py_webauthn, never hand-written).

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
from openstore.core.policy_signing import PER_USER_AGGREGATE_CAP_MINOR
from openstore.core.webauthn_rp import ChallengeStore

ROOT = Path(__file__).resolve().parent.parent.parent  # noqa: I001
sys.path.insert(0, str(ROOT / "scripts"))

import make_webauthn_fixtures as wf  # noqa: E402, I001

OPERATOR = "merchant-1"
HEADERS = {"X-Operator-Id": OPERATOR}

_ENROLLED_CRED_ID = b"\x0f" + b"\x01" * 31


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
            bot_token="t", buyer_trace_channel_id=1, merchant_trace_channel_id=2,
            money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="openstore.test", rp_name="x", origin="https://openstore.test"),
        database=DatabaseConfig(url="sqlite://"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


@pytest.fixture()
def session_factory(rp_settings: Settings) -> callable:
    # The database engine is a process-global singleton in
    # openstore.core.database; reset it so each test starts from a fresh
    # in-memory schema (otherwise shared-state collisions on
    # webauthn_credentials.credential_id).
    import openstore.core.database as _db

    prev = _db._engine
    _db._engine = None
    init_database(rp_settings)
    try:
        yield lambda: get_session(rp_settings)
    finally:
        # Close any connections held by the fresh engine and restore the prior
        # singleton so neighbouring test files are unaffected.
        from openstore.core.database import get_engine

        get_engine(rp_settings).dispose()
        _db._engine = prev


@pytest.fixture()
def client(rp_settings: Settings, session_factory) -> TestClient:
    from openstore.surfaces.studio import policy_studio_router

    store = ChallengeStore()
    app = FastAPI()
    app.include_router(policy_studio_router(rp_settings, session_factory=session_factory, challenge_store=store))
    with TestClient(app) as c:
        yield c


def _policy_fields(max_spend_total_minor: int = 200_000, policy_version: int = 2):
    return {
        "policy_version": policy_version,
        "currency": "INR",
        "max_spend_per_tx_minor": 50_000,
        "max_spend_total_minor": max_spend_total_minor,
        "max_transactions": 10,
        "allowed_tags": ["vegan"],
        "tag_mode": "all",
        "blocked_skus": [],
        "not_before": 1_700_000_000,
        "expires_at": 1_800_000_000,
        "assertion_max_age_seconds": 86400,
        "fulfilment_mode": "all_or_nothing",
        "required_skus": [],
    }


def _enrol(client: TestClient) -> None:
    ec, _, _ = wf.keys()
    begin = client.post("/internal/webauthn/register/begin",
                        json={"user_name": OPERATOR}, headers=HEADERS)
    assert begin.status_code == 200
    challenge = _b64d(begin.json()["challenge"])
    cd, att = wf.build_registration(_ENROLLED_CRED_ID, challenge, wf.cose_ec2_p256_public(ec, -7), 3)
    res = client.post(
        "/internal/webauthn/register/complete",
        json={
            "credential_id": _b64u(_ENROLLED_CRED_ID),
            "client_data_json": _b64u(cd),
            "attestation_object": _b64u(att),
            "challenge": begin.json()["challenge"],
        },
        headers=HEADERS,
    )
    assert res.status_code == 200, res.text


def _assert_for(challenge_b64: str, sign_count: int = 4):
    ec, _, _ = wf.keys()
    cd, auth, sig = wf.build_assertion(
        _ENROLLED_CRED_ID, _b64d(challenge_b64), sign_count, ec, wf.sign_es256
    )
    return {
        "credential_id": _b64u(_ENROLLED_CRED_ID),
        "client_data_json": _b64u(cd),
        "authenticator_data": _b64u(auth),
        "signature": _b64u(sig),
    }


def _begin_assertion(client: TestClient) -> str:
    res = client.post("/internal/webauthn/assertion/begin", json={}, headers=HEADERS)
    assert res.status_code == 200
    return res.json()["challenge"]


# --- page + session --------------------------------------------------------


def test_studio_requires_operator_session(client: TestClient):
    res = client.get("/intent/studio")
    assert res.status_code == 401


def test_studio_page_renders_hold_table_and_cap(client: TestClient):
    res = client.get("/intent/studio", headers=HEADERS)
    assert res.status_code == 200
    html = res.text
    assert "HOLD_SECONDS" in html or "Hold duration" in html
    # AAL3=0, AAL2=900, AAL1=3600 must render before the human signs.
    assert "900s" in html
    assert "3600s" in html
    assert "0s" in html
    assert str(PER_USER_AGGREGATE_CAP_MINOR) in html
    assert "policy.aggregate_cap_exceeded" in html


# --- enrolment + assertion via router --------------------------------------


def test_register_and_sign_happy_path(client: TestClient, session_factory):
    _enrol(client)

    challenge = _begin_assertion(client)
    assertion = _assert_for(challenge)

    res = client.post(
        "/internal/webauthn/assertion/complete",
        json={**assertion, "challenge": challenge, "policy": _policy_fields()},
        headers=HEADERS,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["policy_id"].startswith("pol_")
    assert len(body["policy_hash"]) == 64

    # Blast radius reflects 1 policy + 1 credential, no freeze.
    br = client.get("/internal/policy/blast-radius", headers=HEADERS)
    assert br.status_code == 200
    radius = br.json()
    assert radius["policies"] == 1
    assert radius["credentials"] == 1
    assert radius["projected_freeze"] is False


def test_signing_rejects_aggregate_cap_exceeded(client: TestClient, session_factory):
    _enrol(client)

    # First policy consumes the entire per-user cap.
    challenge = _begin_assertion(client)
    a1 = _assert_for(challenge)
    res1 = client.post(
        "/internal/webauthn/assertion/complete",
        json={**a1, "challenge": challenge, "policy": _policy_fields(max_spend_total_minor=PER_USER_AGGREGATE_CAP_MINOR)},
        headers=HEADERS,
    )
    assert res1.status_code == 200, res1.text

    # Second policy would push aggregate above the cap -> policy.aggregate_cap_exceeded.
    challenge2 = _begin_assertion(client)
    a2 = _assert_for(challenge2, sign_count=5)
    res2 = client.post(
        "/internal/webauthn/assertion/complete",
        json={**a2, "challenge": challenge2, "policy": _policy_fields(max_spend_total_minor=1000)},
        headers=HEADERS,
    )
    assert res2.status_code == 422
    assert res2.json()["detail"]["reason_code"] == "policy.aggregate_cap_exceeded"


def test_signing_rejects_legacy_policy_version(client: TestClient, session_factory):
    _enrol(client)

    challenge = _begin_assertion(client)
    assertion = _assert_for(challenge)
    res = client.post(
        "/internal/webauthn/assertion/complete",
        json={**assertion, "challenge": challenge, "policy": _policy_fields(policy_version=1)},
        headers=HEADERS,
    )
    assert res.status_code == 422
    assert res.json()["detail"]["reason_code"] == "policy.policy_version_unsupported"


def test_assertion_without_policy_returns_sign_count(client: TestClient, session_factory):
    _enrol(client)

    challenge = _begin_assertion(client)
    assertion = _assert_for(challenge)
    res = client.post(
        "/internal/webauthn/assertion/complete",
        json={**assertion, "challenge": challenge, "policy": None},
        headers=HEADERS,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["sign_count"] == 4


def test_reuse_of_challenge_rejected(client: TestClient, session_factory):
    _enrol(client)

    challenge = _begin_assertion(client)
    assertion = _assert_for(challenge)
    payload = {**assertion, "challenge": challenge, "policy": _policy_fields()}
    res1 = client.post("/internal/webauthn/assertion/complete", json=payload, headers=HEADERS)
    assert res1.status_code == 200, res1.text
    res2 = client.post("/internal/webauthn/assertion/complete", json=payload, headers=HEADERS)
    assert res2.status_code == 401
    assert res2.json()["detail"]["reason_code"] == "assertion_required"
