import time
from datetime import timezone

from openstore.core import did, envelope
from openstore.ledger import Ledger
from openstore.models import (
    Mandate,
    ReceiptKind,
    TrustReceipt,
)


def _keypair():
    priv, pub = did.generate_keypair()
    return priv, did.did_from_pubkey(pub)


def test_mandate_sign_verify():
    priv, d = _keypair()
    m = Mandate.create(
        private_key=priv,
        mandate_id="M1",
        merchant_did="did:key:zmerchant",
        payer=d,
        scope="global",
        max_amount_paise=100000,
        currency="INR",
        expires_at=int(time.time()) + 86400,
    )
    m.verify(priv.public_key())  # no raise


def test_ledger_anchor_and_inclusion():
    ledger = Ledger()
    priv, d = _keypair()
    now = int(time.time())
    for i in range(5):
        env = envelope.AgentTrustEnvelope(
            issuer_did=d,
            issued_at=now,
            expires_at=now + 3600,
            payload_type="application/json",
            payload=f'{{"i":{i}}}'.encode(),
        ).sign(priv)
        ledger.append(
            TrustReceipt(
                receipt_id=f"R{i:03d}",
                kind=ReceiptKind.ORDER,
                ts=now,
                actor_did=d,
                envelope=env.to_dict(),
                payload_ref="h",
            )
        )
    date = __import__("datetime").datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
    root, anchored = ledger.anchor_day(date, b"secret")
    assert anchored is not None
    for i in range(5):
        assert ledger.verify_receipt_inclusion(f"R{i:03d}", root)
    assert not ledger.verify_receipt_inclusion("R999", root)
