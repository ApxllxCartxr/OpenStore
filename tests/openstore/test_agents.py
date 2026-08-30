import asyncio
import time

from openstore.core import did
from openstore.gateway import FakeGateway
from openstore.models import Mandate, Policy, QuoteItem
from openstore.runtime import MerchantRuntime
from openstore.client import OpenStoreClient, build_signed_mandate
from reference.buyer_agent.openstore_agent import BuyerAgent, purchase
from reference.merchant_agent.agent import MerchantAgent


def _rt():
    priv, _ = did.generate_keypair()
    return MerchantRuntime(
        merchant_signing_key=priv,
        legal_name="Gelateria",
        country="IN",
        per_txn_limit=1_000_000_00,
        daily_limit=5_000_000_00,
        catalog={"SKU1": {"title": "Latte", "blocked": False, "unit_price_paise": 25000}},
        policy=Policy(spend_limit_paise=500_000_00),
        gateway=FakeGateway(),
    )


def _payer():
    return did.generate_keypair()[0]


def test_buyer_agent_purchase_and_verify():
    rt = _rt()
    payer = _payer()
    out = purchase(rt, payer, [{"sku": "SKU1", "title": "Latte", "quantity": 2, "unit_price_paise": 25000}],
                   max_amount_paise=100_000_00)
    assert out["order"]["receipt_id"].startswith("R")
    assert out["trust_verification"]["valid"] is True
    assert out["trust_verification"]["inclusion"] is True
    assert out["daily_anchor"]


def test_merchant_agent_verify_and_dispute():
    rt = _rt()
    payer = _payer()
    out = purchase(rt, payer, [{"sku": "SKU1", "title": "Latte", "quantity": 1, "unit_price_paise": 25000}],
                   max_amount_paise=100_000_00)
    rid = out["order"]["receipt_id"]
    ma = MerchantAgent(rt)
    v = ma.verify_receipt(rid)
    assert v["verdict"] == "authentic"
    d = ma.assess_dispute(out["order"]["order_id"], "item defective", claimed_amount_paise=25000)
    assert d["recommendation"] == "accept"


def test_dispute_return_release_refund_flows():
    rt = _rt()
    payer = _payer()
    out = purchase(rt, payer, [{"sku": "SKU1", "title": "Latte", "quantity": 1, "unit_price_paise": 25000}],
                   max_amount_paise=100_000_00)
    oid = out["order"]["order_id"]
    assert rt.raise_dispute(oid, "buyer", "defective")["dispute_id"].startswith("D")
    assert rt.return_order(oid, ["SKU1"], "changed mind")["receipt_id"].startswith("R")
    assert rt.release_order(oid, 25000, "merchant")["released"] == 25000
    refund = asyncio.run(rt.refund_order(oid, "buyer request"))
    assert refund["refund"] == "refunded"
    assert rt.orders[oid].status == "refunded"


def test_mandate_policy_check():
    rt = _rt()
    payer = _payer()
    payer_did = did.did_from_pubkey(payer.public_key())
    m = build_signed_mandate(
        payer_priv=payer, mandate_id="M1", merchant_did=rt.did,
        scope="global", max_amount_paise=10_000_00, currency="INR",
        expires_at=int(time.time()) + 86400,
    )
    ma = MerchantAgent(rt)
    assert ma.check_mandate_against_policy(m)["approved"] is True
