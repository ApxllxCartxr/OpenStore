import asyncio

from openstore.core import did, envelope
from openstore.gateway import FakeGateway
from openstore.models import Mandate, Policy, QuoteItem
from openstore.runtime import MerchantRuntime


def _build():
    mpriv, mpub = did.generate_keypair()
    rt = MerchantRuntime(
        merchant_signing_key=mpriv,
        legal_name="Gelateria",
        country="IN",
        per_txn_limit=1_000_000_00,
        daily_limit=5_000_000_00,
        catalog={"SKU1": {"title": "Latte", "blocked": False}},
        policy=Policy(spend_limit_paise=500_000_00),
        gateway=FakeGateway(),
    )
    return rt, mpriv


def test_discover_has_did():
    rt, _ = _build()
    doc = rt.discover()
    assert doc.trust_anchor.did == rt.did
    assert "create_order" in doc.supported_actions


def test_e2e_order_and_receipt():
    rt, _ = _build()
    ppriv, ppub = did.generate_keypair()
    payer = did.did_from_pubkey(ppub)

    mandate = Mandate.create(
        private_key=ppriv,
        mandate_id="M1",
        merchant_did=rt.did,
        payer=payer,
        scope="global",
        max_amount_paise=100_000_00,
        currency="INR",
        expires_at=int(__import__("time").time() + 86400),
    )
    m_receipt = rt.submit_mandate(mandate)
    assert rt.get_trust_receipt(m_receipt) is not None

    quote = rt.create_quote([QuoteItem(sku="SKU1", title="Latte", quantity=2, unit_price_paise=25000, tax_paise=0)])
    assert quote.total_paise == 50000

    res = asyncio.run(rt.create_order(quote, mandate))
    assert res["order_id"].startswith("O")
    receipt = rt.get_trust_receipt(res["receipt_id"])
    env = envelope.AgentTrustEnvelope.from_dict(receipt.envelope)
    env.verify()  # merchant signature valid
    assert env.issuer_did == rt.did

    cancel = asyncio.run(rt.cancel_order(res["order_id"], "changed mind"))
    assert cancel["refund"] == "refunded"


def test_e2e_anchor_and_inclusion():
    rt, _ = _build()
    ppriv, ppub = did.generate_keypair()
    payer = did.did_from_pubkey(ppub)
    mandate = Mandate.create(
        private_key=ppriv, mandate_id="M2", merchant_did=rt.did, payer=payer,
        scope="global", max_amount_paise=100_000_00, currency="INR",
        expires_at=int(__import__("time").time() + 86400),
    )
    rt.submit_mandate(mandate)
    quote = rt.create_quote([QuoteItem(sku="SKU1", title="Latte", quantity=1, unit_price_paise=25000)])
    res = asyncio.run(rt.create_order(quote, mandate))
    anchored = rt.anchor_today()
    root = bytes.fromhex(anchored["root"])
    assert rt.ledger.verify_receipt_inclusion(res["receipt_id"], root)


def test_blocked_sku_rejected():
    rt, _ = _build()
    rt.policy.blocked_skus = ["SKU1"]
    try:
        rt.create_quote([QuoteItem(sku="SKU1", title="x", quantity=1, unit_price_paise=100)])
        assert False
    except ValueError as e:
        assert "blocked" in str(e)
