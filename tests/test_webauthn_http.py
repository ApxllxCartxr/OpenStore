import secrets
import time

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from openstore.client import build_signed_mandate
from openstore.core.authority.native_webauthn import build_none_attestation, simulate_browser_assertion
from openstore.models import Policy, Quote
from openstore.runtime import MerchantRuntime
from openstore.server import create_http_app


def _rt(tmp_path):
    return MerchantRuntime(
        merchant_signing_key=Ed25519PrivateKey.generate(),
        legal_name="Gelateria", country="IN",
        per_txn_limit=1_000_000_00, daily_limit=5_000_000_00,
        catalog={"GELATO": {"unit_price_paise": 5000, "tags": ["food"]}},
        policy=Policy(spend_limit_paise=1_000_000_00),
        rp_id="openstore.local", origin="https://openstore.local",
        ledger_url=f"sqlite:///{tmp_path / 'ledger.db'}",
    )


def test_http_webauthn_ceremony(tmp_path):
    rt = _rt(tmp_path)
    client = TestClient(create_http_app(rt))

    # 1. Register a real none-fmt credential (browser equivalent).
    cred_key = ec.generate_private_key(ec.SECP256R1())
    cred_id = secrets.token_bytes(16)
    begin = client.post("/webauthn/register/begin").json()
    att = build_none_attestation(
        signer_key=cred_key, rp_id=rt.rp_id,
        challenge_b64=begin["challenge"], origin=rt.origin, credential_id=cred_id,
    )
    reg = client.post("/webauthn/register/complete", json={**att, "session_id": begin["session_id"]}).json()
    assert reg["credential_id"]

    # 2. Quote + begin ceremony.
    quote = client.post("/quote", json={"items": [
        {"sku": "GELATO", "title": "Gelato", "quantity": 2, "unit_price_paise": 5000, "tax_paise": 0}
    ]}).json()
    begin2 = client.post("/webauthn/begin", json={"quote": quote}).json()
    oc = rt._order_context(Quote.model_validate(quote))
    assertion = simulate_browser_assertion(
        rp_id=rt.rp_id, origin=rt.origin, challenge_b64=begin2["challenge"],
        policy=oc["policy_dict"], cart_hash=oc["cart_hash"],
        credential_id=cred_id, signer_key=cred_key,
    )
    authority = client.post("/webauthn/complete", json={
        "session_id": begin2["session_id"], "assertion": assertion, "quote": quote,
    }).json()
    assert authority["webauthn"]["uv"] is True

    # 3. Order with the verified authority + a payer-signed mandate.
    mandate = build_signed_mandate(
        payer_priv=Ed25519PrivateKey.generate(), mandate_id="M1", merchant_did=rt.did,
        scope="global", max_amount_paise=100_000, currency="INR", expires_at=int(time.time()) + 3600,
    )
    order = client.post("/order", json={"quote": quote, "mandate": mandate.model_dump(), "authority": authority}).json()
    assert order["aal_level"] == 3

    vr = client.post("/verify", json={"receipt_id": order["receipt_id"]}).json()
    assert vr["valid"] is True
    assert vr["poai"]["ok"] is True
    assert vr["poai"]["failures"] == []
