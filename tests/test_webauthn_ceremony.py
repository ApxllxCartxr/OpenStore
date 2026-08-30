import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openstore.client import OpenStoreClient, build_signed_mandate
from openstore.core.authority import webauthn_rp
from openstore.core.authority.native_webauthn import simulate_browser_assertion
from openstore.evidence import b64url, b64url_decode
from openstore.models import Policy, Quote
from openstore.runtime import MerchantRuntime


def _rt():
    return MerchantRuntime(
        merchant_signing_key=Ed25519PrivateKey.generate(),
        legal_name="Gelateria",
        country="IN",
        per_txn_limit=1_000_000_00,
        daily_limit=5_000_000_00,
        catalog={"GELATO": {"unit_price_paise": 5000, "tags": ["food"]}},
        policy=Policy(spend_limit_paise=1_000_000_00),
        rp_id="openstore.local",
        origin="https://openstore.local",
    )


def _quote(client):
    return client.create_quote(
        [{"sku": "GELATO", "title": "Gelato", "quantity": 2, "unit_price_paise": 5000, "tax_paise": 0}]
    )


def test_full_webauthn_ceremony():
    rt = _rt()
    client = OpenStoreClient(rt)
    quote = _quote(client)

    # 1. Merchant issues a challenge bound to this cart + policy.
    begin = client.rt.webauthn.begin_assertion(
        *(_order_policy_cart(rt, quote))
    )
    session_id = begin["session_id"]

    # 2. Browser (simulated) signs the challenge with a registered authenticator.
    cred_id, cred_key = rt.webauthn._dev
    assertion = simulate_browser_assertion(
        rp_id=rt.rp_id, origin=rt.origin, challenge_b64=begin["challenge"],
        policy=rt._order_context(Quote.model_validate(quote))["policy_dict"],
        cart_hash=rt._order_context(Quote.model_validate(quote))["cart_hash"],
        credential_id=cred_id, signer_key=cred_key,
    )
    assertion["session_id"] = session_id

    # 3. Merchant verifies the assertion -> PoAI authority.
    authority = client.rt.webauthn.complete_assertion(
        session_id, assertion,
        policy=rt._order_context(Quote.model_validate(quote))["policy_dict"],
        cart_hash=rt._order_context(Quote.model_validate(quote))["cart_hash"],
    )
    assert authority["webauthn"]["uv"] is True

    # 4. Order carries the verified authority; standalone verifier accepts it.
    expires = int(time.time()) + 3600
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    mandate = build_signed_mandate(
        payer_priv=Ed25519PrivateKey.generate(), mandate_id="M1", merchant_did=rt.did,
        scope="global", max_amount_paise=100_000, currency="INR", expires_at=expires,
    )
    res = client.create_order(quote, mandate, authority=authority)
    assert res["aal_level"] == 3

    vr = client.verify_receipt(res["receipt_id"], rt.did)
    assert vr["valid"] is True
    assert vr["poai"]["ok"] is True
    assert vr["poai"]["failures"] == []


def test_tampered_assertion_is_rejected():
    rt = _rt()
    begin = rt.webauthn.begin_assertion(
        rt._order_context(Quote.model_validate(_quote(OpenStoreClient(rt))))["policy_dict"],
        rt._order_context(Quote.model_validate(_quote(OpenStoreClient(rt))))["cart_hash"],
    )
    cred_id, cred_key = rt.webauthn._dev
    oc = rt._order_context(Quote.model_validate(_quote(OpenStoreClient(rt))))
    assertion = simulate_browser_assertion(
        rp_id=rt.rp_id, origin=rt.origin, challenge_b64=begin["challenge"],
        policy=oc["policy_dict"], cart_hash=oc["cart_hash"],
        credential_id=cred_id, signer_key=cred_key,
    )
    # Tamper: flip a byte in the signature.
    sig = bytearray(b64url_decode(assertion["response"]["signature"]))
    sig[0] ^= 0xFF
    assertion["response"]["signature"] = b64url(bytes(sig))
    try:
        rt.webauthn.complete_assertion(
            begin["session_id"], assertion, policy=oc["policy_dict"], cart_hash=oc["cart_hash"]
        )
        assert False, "expected signature rejection"
    except webauthn_rp.WebAuthnError:
        pass


def _order_policy_cart(rt, quote):
    oc = rt._order_context(Quote.model_validate(quote))
    return oc["policy_dict"], oc["cart_hash"]
