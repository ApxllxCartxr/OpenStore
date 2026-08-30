import asyncio
from typing import Dict, List, Optional

from openstore.core import did as did_mod, envelope as env_mod
from openstore.models import Mandate, Quote, QuoteItem, ReceiptKind, TrustReceipt
from openstore.runtime import MerchantRuntime


def build_signed_mandate(
    *,
    payer_priv,
    mandate_id: str,
    merchant_did: str,
    scope: str,
    max_amount_paise: int,
    currency: str,
    expires_at: int,
) -> Mandate:
    payer = did_mod.did_from_pubkey(payer_priv.public_key())
    return Mandate.create(
        private_key=payer_priv,
        mandate_id=mandate_id,
        merchant_did=merchant_did,
        payer=payer,
        scope=scope,
        max_amount_paise=max_amount_paise,
        currency=currency,
        expires_at=expires_at,
    )


class OpenStoreClient:
    """In-process client that lets a buyer agent talk to a merchant runtime using
    the generic OpenStore protocol. (A remote HTTP variant wraps the same methods
    against a merchant's /agent/mcp endpoint.)"""

    def __init__(self, runtime: MerchantRuntime):
        self.rt = runtime

    def discover(self) -> dict:
        return self.rt.discover().model_dump()

    def create_quote(self, items: List[dict]) -> dict:
        q = self.rt.create_quote([QuoteItem.model_validate(i) for i in items])
        return q.model_dump()

    def submit_mandate(self, mandate: Mandate) -> dict:
        return {"receipt_id": self.rt.submit_mandate(mandate), "mandate_id": mandate.mandate_id}

    def create_order(self, quote: dict, mandate: Mandate, authority: dict = None, webauthn_assertion: dict = None, delegation_spec=None, confirmation=None) -> dict:
        return asyncio.run(
            self.rt.create_order(
                Quote.model_validate(quote), mandate,
                authority=authority, webauthn_assertion=webauthn_assertion,
                delegation_spec=delegation_spec, confirmation=confirmation,
            )
        )

    def preview_delegated_order(self, quote: dict, delegation_spec) -> dict:
        return self.rt.preview_delegated_order(Quote.model_validate(quote), delegation_spec)

    def begin_confirmation(self, preview_hash: str) -> dict:
        return self.rt.begin_confirmation(preview_hash)

    def cancel_order(self, order_id: str, reason: str) -> dict:
        return asyncio.run(self.rt.cancel_order(order_id, reason))

    def refund_order(self, order_id: str, reason: str) -> dict:
        return asyncio.run(self.rt.refund_order(order_id, reason))

    def raise_dispute(self, order_id: str, raised_by: str, reason: str, evidence=None) -> dict:
        return self.rt.raise_dispute(order_id, raised_by, reason, evidence)

    def get_trust_receipt(self, receipt_id: str) -> Optional[TrustReceipt]:
        return self.rt.get_trust_receipt(receipt_id)

    def anchor_today(self) -> dict:
        return self.rt.anchor_today()

    def verify_receipt(self, receipt_id: str, merchant_did: str, root: Optional[bytes] = None) -> dict:
        """Independently verify a merchant trust receipt: envelope signature,
        expiry, and (if a daily root is supplied) Merkle inclusion."""
        receipt = self.rt.get_trust_receipt(receipt_id)
        if receipt is None:
            return {"valid": False, "reason": "missing receipt"}
        env = env_mod.AgentTrustEnvelope.from_dict(receipt.envelope)
        try:
            env.verify()
        except Exception as e:
            return {"valid": False, "reason": f"envelope: {e}"}
        if env.issuer_did != merchant_did:
            return {"valid": False, "reason": "issuer did mismatch"}
        result = {"valid": True, "issuer": env.issuer_did, "kind": receipt.kind.value}
        if root is not None:
            result["inclusion"] = self.rt.ledger.verify_receipt_inclusion(receipt_id, root)
        if getattr(env, "poai_bundle", None):
            from openstore.evidence import public_jwk
            from openstore.verify import verify_bundle

            jwks = {"keys": [public_jwk(self.rt.attest_key.public_key(), merchant_did)]}
            vr = verify_bundle(env.poai_bundle, jwks)
            result["poai"] = {
                "ok": vr.ok,
                "aal_level": vr.re_derived.get("aal_level"),
                "checks": [(c["name"], c["result"]) for c in vr.checks],
                "failures": vr.failures,
            }
        return result
