"""Reference merchant (reasoning) agent.

Uses the generic OpenStore core to verify trust receipts, sanity-check mandates
against policy, and reason about disputes/returns. This is the merchant-side
"brain" that an operator or the storefront consults — it does not move money,
it only reasons over the verifiable ledger.
"""
from typing import Dict, List

from openstore.core import envelope as env_mod
from openstore.models import Policy, ReceiptKind
from openstore.runtime import _now


class MerchantAgent:
    def __init__(self, runtime, policy: Policy = None):
        self.rt = runtime
        self.policy = policy or runtime.policy

    def verify_receipt(self, receipt_id: str) -> Dict:
        receipt = self.rt.get_trust_receipt(receipt_id)
        if receipt is None:
            return {"verdict": "unknown", "reason": "no such receipt"}
        env = env_mod.AgentTrustEnvelope.from_dict(receipt.envelope)
        try:
            env.verify()
            ok = True
            reason = "signature valid"
        except Exception as e:
            ok = False
            reason = str(e)
        return {
            "verdict": "authentic" if ok else "rejected",
            "reason": reason,
            "issuer": env.issuer_did,
            "kind": receipt.kind.value,
            "ts": receipt.ts,
        }

    def check_mandate_against_policy(self, mandate) -> Dict:
        problems = []
        if mandate.expires_at < _now():
            problems.append("mandate expired")
        if self.policy.spend_limit_paise and mandate.max_amount_paise > self.policy.spend_limit_paise:
            problems.append("mandate exceeds merchant spend limit")
        if mandate.currency != "INR":
            problems.append("unsupported currency")
        return {"approved": not problems, "problems": problems}

    def assess_dispute(self, order_id: str, reason: str, claimed_amount_paise: int = 0) -> Dict:
        order = self.rt.orders.get(order_id)
        if order is None:
            return {"recommendation": "reject", "reason": "no such order"}
        paid = order.quote.total_paise
        if claimed_amount_paise and claimed_amount_paise > paid:
            return {"recommendation": "reject", "reason": "claimed amount exceeds amount paid"}
        if order.status in ("refunded", "cancelled"):
            return {"recommendation": "reject", "reason": f"order already {order.status}"}
        return {
            "recommendation": "accept",
            "reason": "order valid and unpaid-out; offer refund or return",
            "order_total_paise": paid,
        }

    def daily_assurance(self) -> Dict:
        anchor = self.rt.anchor_today()
        return {
            "date": anchor["date"],
            "daily_anchor": anchor["anchor"],
            "root": anchor["root"],
            "note": "all of today's receipts are committed to this anchor; share the "
            "anchor (or the daily seed) with auditors/buyers for independent verification.",
        }
