import asyncio
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openstore.client import OpenStoreClient, build_signed_mandate
from openstore.models import Policy, QuoteItem
from openstore.runtime import MerchantRuntime


def _rt():
    key = Ed25519PrivateKey.generate()
    catalog = {"GELATO": {"unit_price_paise": 5000, "tags": ["food"]}}
    return MerchantRuntime(
        merchant_signing_key=key,
        legal_name="Gelateria",
        country="IN",
        per_txn_limit=1_000_000_00,
        daily_limit=5_000_000_00,
        catalog=catalog,
        policy=Policy(spend_limit_paise=1_000_000_00),
    )


def test_order_carries_verifiable_poai_bundle():
    rt = _rt()
    client = OpenStoreClient(rt)

    quote = client.create_quote(
        [{"sku": "GELATO", "title": "Gelato", "quantity": 2, "unit_price_paise": 5000, "tax_paise": 0}]
    )

    expires = int(time.time()) + 3600
    mandate = build_signed_mandate(
        payer_priv=Ed25519PrivateKey.generate(),
        mandate_id="M1",
        merchant_did=rt.did,
        scope="global",
        max_amount_paise=100_000,
        currency="INR",
        expires_at=expires,
    )

    res = client.create_order(quote, mandate)
    assert "poai_bundle" in res
    assert res["aal_level"] == 3

    vr = client.verify_receipt(res["receipt_id"], rt.did)
    assert vr["valid"] is True
    assert vr["poai"]["ok"] is True
    assert vr["poai"]["aal_level"] == 3
    assert vr["poai"]["failures"] == []
