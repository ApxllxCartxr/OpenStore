"""Reference buyer agent built on the generic OpenStore protocol.

This is the agent-centric shopping flow: discover the merchant, obtain a payer
mandate, price a cart, place an order, then *independently verify* the merchant's
trust receipt against the daily Merkle anchor. It works in-process against any
MerchantRuntime (including the reference merchant), and the same protocol works
over MCP against a remote merchant.
"""
import time
from typing import Dict, List

from openstore.client import OpenStoreClient, build_signed_mandate
from openstore.core import did as did_mod


class BuyerAgent:
    def __init__(self, payer_priv, client: OpenStoreClient, agent_id: str = "buyer-agent"):
        self.payer_priv = payer_priv
        self.payer_did = did_mod.did_from_pubkey(payer_priv.public_key())
        self.client = client
        self.agent_id = agent_id
        self._mandate_seq = 0

    def shop(self, items: List[dict], *, max_amount_paise: int, currency: str = "INR") -> Dict:
        discovery = self.client.discover()
        merchant_did = discovery["trust_anchor"]["did"]

        self._mandate_seq += 1
        mandate = build_signed_mandate(
            payer_priv=self.payer_priv,
            mandate_id=f"M{self._mandate_seq}-{self.agent_id}",
            merchant_did=merchant_did,
            scope="global",
            max_amount_paise=max_amount_paise,
            currency=currency,
            expires_at=int(time.time()) + 86400,
        )
        self.client.submit_mandate(mandate)

        quote = self.client.create_quote(items)
        order = self.client.create_order(quote, mandate)

        anchor = self.client.anchor_today()
        root = bytes.fromhex(anchor["root"])
        verify = self.client.verify_receipt(order["receipt_id"], merchant_did, root)

        return {
            "agent": self.agent_id,
            "payer": self.payer_did,
            "merchant": merchant_did,
            "quote_total_paise": quote["total_paise"],
            "order": order,
            "trust_verification": verify,
            "daily_anchor": anchor["anchor"],
        }


def purchase(runtime, payer_priv, items, *, max_amount_paise, currency="INR", agent_id="buyer-agent"):
    """Convenience one-shot: build a client + agent and shop."""
    return BuyerAgent(payer_priv, OpenStoreClient(runtime), agent_id=agent_id).shop(
        items, max_amount_paise=max_amount_paise, currency=currency
    )
