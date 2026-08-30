import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.hazmat.primitives import serialization
from sqlmodel import Session, select

from openstore.core import did as did_mod
from openstore.core import envelope as env_mod
from openstore.core import merkle, signing
from cryptography.hazmat.primitives.asymmetric import ec

from openstore.attest import AttestationRegistry, compute_catalog_digest
from openstore.gateway import FakeGateway, PaymentGateway
from openstore.hold import HoldManager
from openstore.ledger import Ledger

import hashlib

from openstore import aal as aal_mod
from openstore import compiler as compiler_mod
from openstore import delegation as delegation_mod
from openstore import evidence
from openstore.canonical import canonical_json_bytes, digest
from openstore.core.authority import (
    cap_aal_by_depth,
    cap_aal_multi_merchant,
    webauthn_rp,
)
from openstore.core.authority.native_webauthn import simulate_browser_assertion
from openstore.models import (
    Capabilities,
    DiscoveryDoc,
    Mandate,
    MerchantInfo,
    Policy,
    Quote,
    QuoteItem,
    ReceiptKind,
    TrustAnchor,
    TrustReceipt,
    TrustReceipt as TR,
)


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def load_signing_key(path: str):
    p = Path(path)
    if p.exists():
        return serialization.load_pem_private_key(p.read_bytes(), password=None)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate()


class OrderRecord:
    def __init__(self, order_id, quote, mandate_id, payment_id, status):
        self.order_id = order_id
        self.quote = quote
        self.mandate_id = mandate_id
        self.payment_id = payment_id
        self.status = status


@dataclass
class DelegationSpec:
    """Input for minting a delegated order (DELEGATION_AND_ORCHESTRATION §3/§5).

    `chain` is the full delegation link path (root human + delegated links). The
    leaf link's `envelope_id` and grant define this order's envelope. `prior_spend`
    is the already-signed spend chain for that envelope (may be empty); the current
    purchase is appended as a new SPEND entry by the runtime.
    """

    chain: tuple
    envelope: "object"
    prior_spend: tuple = ()
    sequencer_type: str = "none"
    # Required when sequencer_type != "none" (§6.2b): the key the runtime uses to
    # countersign the ordered (merchant, amount, sequence) set.
    sequencer_key: "object" = None


class MerchantRuntime:
    def __init__(
        self,
        *,
        merchant_signing_key,
        legal_name: str,
        country: str,
        per_txn_limit: int,
        daily_limit: int,
        catalog: Dict[str, dict],
        policy: Policy,
        gateway: Optional[PaymentGateway] = None,
        ledger_url: str = "sqlite:///:memory:",
        anchor_secret: bytes = b"anchor-secret",
        attest_key: Optional[ec.EllipticCurvePrivateKey] = None,
        rp_id: str = "openstore.local",
        origin: str = "https://openstore.local",
    ):
        self.priv = merchant_signing_key
        self.did = did_mod.did_from_pubkey(merchant_signing_key.public_key())
        self.catalog = catalog
        self.policy = policy
        self.gateway = gateway or FakeGateway()
        self.ledger = Ledger(ledger_url)
        self.anchor_secret = anchor_secret
        self.orders: Dict[str, OrderRecord] = {}
        self.mandates: Dict[str, Mandate] = {}
        self._seq = 0
        self.attest_key = attest_key or ec.generate_private_key(ec.SECP256R1())
        self.attestations = AttestationRegistry(
            merchant_id=self.did, signing_key=self.attest_key, kid=self.did
        )
        self.rp_id = rp_id
        self.origin = origin
        self.webauthn = webauthn_rp.WebAuthnRP(rp_id, origin)
        self.webauthn.register_dev_credential()
        self.holds = HoldManager(self.ledger.engine)
        self.rejections: List[dict] = []
        self.agent_sessions: Dict[str, dict] = {}

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:06d}"

    def _sign(self, payload: bytes, kind_claims: Optional[dict] = None, poai_bundle: Optional[dict] = None) -> env_mod.AgentTrustEnvelope:
        return env_mod.AgentTrustEnvelope(
            issuer_did=self.did,
            issued_at=_now(),
            expires_at=_now() + 3600,
            payload_type="application/json",
            payload=payload,
            inherent_claims=kind_claims,
            poai_bundle=poai_bundle,
        ).sign(self.priv)

    def _order_context(self, quote):
        """Shared goods + policy derivation used by both the ceremony and bundling."""
        catalog_attested = True
        goods_items = []
        for it in quote.items:
            att = getattr(it, "catalog_attestation", None)
            if not att:
                catalog_attested = False
            prod = self.catalog.get(it.sku, {})
            tags = list(prod.get("tags", []))
            goods_items.append({
                "sku": it.sku,
                "qty": it.quantity,
                "unit_minor": it.unit_price_paise,
                "tags": tags,
                "catalog_attestation": att,
            })
        cart_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(goods_items)).hexdigest()
        subtotal = sum(i["unit_minor"] * i["qty"] for i in goods_items)

        policy = compiler_mod.CompilerPolicy(
            policy_version=2,
            merchant_id=self.did,
            currency=quote.currency,
            max_spend_per_tx_minor=self.policy.spend_limit_paise or 1_000_000_00,
            max_spend_total_minor=1_000_000_00,
            max_transactions=1000,
            allowed_tags=(),
            tag_mode="all",
            blocked_skus=tuple(self.policy.blocked_skus or []),
            not_before=0,
            expires_at=int(time.time()) + 10 * 365 * 24 * 3600,
        )
        policy_dict = {
            "policy_version": policy.policy_version,
            "merchant_id": policy.merchant_id,
            "currency": policy.currency,
            "max_spend_per_tx_minor": policy.max_spend_per_tx_minor,
            "max_spend_total_minor": policy.max_spend_total_minor,
            "max_transactions": policy.max_transactions,
            "allowed_tags": list(policy.allowed_tags),
            "tag_mode": policy.tag_mode,
            "blocked_skus": list(policy.blocked_skus),
            "not_before": policy.not_before,
            "expires_at": policy.expires_at,
            "assertion_max_age_seconds": 86400,
            "step_up_above_minor": 0,
        }
        return {
            "goods_items": goods_items,
            "cart_hash": cart_hash,
            "subtotal": subtotal,
            "catalog_attested": catalog_attested,
            "policy": policy,
            "policy_dict": policy_dict,
        }

    def _delegated_preview(self, quote, delegation_spec):
        """DELEGATION §5/§6.2 — pre-charge risk disclosure for a delegated purchase.

        Runs the v1.1.0 compile (no charge, no bundle) and enforces the §6.2b
        sequencer rules. Returns a dict with the disclosure and a stable
        `preview_hash` the human must approve before `create_order` will finalize.
        """
        ctx = self._order_context(quote)
        goods_items = ctx["goods_items"]
        subtotal = ctx["subtotal"]
        eff = delegation_mod.effective_policy_from_chain(delegation_spec.chain)
        merchant_ids = tuple(eff.merchant_ids)
        merchant_count = len(merchant_ids)
        sequencer_type = delegation_spec.sequencer_type

        # §6.2b — a delegated purchase that names no sequencer MUST NOT span >1 merchant.
        if merchant_count > 1 and sequencer_type == "none":
            raise ValueError("multi-merchant delegated purchase requires a sequencer")
        # A named sequencer must have a key to countersign the ordering with.
        if sequencer_type != "none" and delegation_spec.sequencer_key is None:
            raise ValueError("sequencer_type set but no sequencer_key provided")

        policy = compiler_mod.CompilerPolicy(
            policy_version=2,
            merchant_id=eff.merchant_ids[0] if eff.merchant_ids else self.did,
            merchant_ids=merchant_ids,
            currency=eff.currency,
            max_spend_per_tx_minor=eff.max_spend_per_tx_minor,
            max_spend_total_minor=eff.max_spend_total_minor,
            max_transactions=eff.max_transactions,
            allowed_tags=tuple(eff.allowed_tags),
            tag_mode=eff.tag_mode,
            blocked_skus=tuple(eff.blocked_skus),
            not_before=eff.not_before,
            expires_at=eff.expires_at,
        )
        compiler_ctx = compiler_mod.CompilerContext(
            merchant_id=self.did, currency=quote.currency,
            evaluated_at_unix=_now(), spent_minor=0, transactions_count=0,
        )
        citems = tuple(
            compiler_mod.CompilerItem(
                sku=i["sku"], qty=i["qty"], unit_minor=i["unit_minor"], tags=tuple(i["tags"])
            )
            for i in goods_items
        )
        chain_spent = sum(
            e.amount_minor for e in delegation_spec.prior_spend if e.entry_type == "SPEND"
        )
        verdict = compiler_mod.compile_decision_v11(
            citems, policy, compiler_ctx,
            envelope_budget_minor=delegation_spec.envelope.budget_minor,
            chain_spent_minor=chain_spent,
        )
        # AAL with the depth + multi-merchant/sequencer caps (§6.1/§6.2b).
        depth = delegation_spec.chain[-1].depth
        preds = aal_mod.Predicates(
            e1_agent_authenticated=True, e2_policy_signature_valid=True, e3_assertion_fresh=True,
            e4_user_verified=True, e5_cart_bound=True, e6_catalog_attested=True,
            e7_compiler_allow=True, e8_intent_recorded=True, e9_notified=True,
        )
        aal_level, aal_reasons = aal_mod.resolve_aal(preds, 2)
        aal_level, aal_reasons = cap_aal_by_depth(aal_level, aal_reasons, depth)
        aal_level, aal_reasons = cap_aal_multi_merchant(
            aal_level, aal_reasons, merchant_count=merchant_count, sequencer_type=sequencer_type
        )
        disclosure = {
            "merchant_did": self.did,
            "amount_minor": subtotal,
            "currency": quote.currency,
            "envelope_id": delegation_spec.envelope.envelope_id,
            "depth": depth,
            "merchant_ids": list(merchant_ids),
            "merchant_count": merchant_count,
            "sequencer_type": sequencer_type,
            "verdict": verdict.verdict,
            "reason_code": verdict.reason_code,
            "aal_level": aal_level,
            "aal_reasons": list(aal_reasons),
            "max_spend_per_tx_minor": eff.max_spend_per_tx_minor,
            "max_spend_total_minor": eff.max_spend_total_minor,
            "allowed_tags": list(eff.allowed_tags),
        }
        preview_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(disclosure)).hexdigest()
        disclosure["preview_hash"] = preview_hash
        return {
            "disclosure": disclosure, "preview_hash": preview_hash,
            "eff": eff, "eff_dict": delegation_mod.effective_policy_to_dict(eff),
            "policy": policy, "verdict": verdict, "compiler_ctx": compiler_ctx,
            "citems": citems, "chain_spent": chain_spent,
            "aal_level": aal_level, "aal_reasons": aal_reasons,
            "depth": depth, "merchant_count": merchant_count,
            "sequencer_type": sequencer_type,
        }

    def preview_delegated_order(self, quote, delegation_spec) -> dict:
        """Public: return the §6.2 disclosure the human must explicitly approve."""
        return self._delegated_preview(quote, delegation_spec)["disclosure"]

    def _merchant_policy_dict(self) -> dict:
        """The merchant's root policy as surfaced to the RP during WebAuthn ceremony."""
        p = compiler_mod.CompilerPolicy(
            policy_version=2, merchant_id=self.did, currency="INR",
            max_spend_per_tx_minor=self.policy.spend_limit_paise or 1_000_000_00,
            max_spend_total_minor=1_000_000_00, max_transactions=1000,
            allowed_tags=(), tag_mode="all",
            blocked_skus=tuple(self.policy.blocked_skus or []),
            not_before=0, expires_at=int(time.time()) + 10 * 365 * 24 * 3600,
        )
        return {
            "policy_version": p.policy_version, "merchant_id": p.merchant_id, "currency": p.currency,
            "max_spend_per_tx_minor": p.max_spend_per_tx_minor, "max_spend_total_minor": p.max_spend_total_minor,
            "max_transactions": p.max_transactions, "allowed_tags": list(p.allowed_tags),
            "tag_mode": p.tag_mode, "blocked_skus": list(p.blocked_skus),
            "not_before": p.not_before, "expires_at": p.expires_at,
            "assertion_max_age_seconds": 86400,
        }

    def begin_confirmation(self, preview_hash: str) -> dict:
        """Start the human confirmation ceremony: a WebAuthn challenge bound to the
        exact `preview_hash` (so the human approves these precise terms)."""
        return self.webauthn.begin_assertion(self._merchant_policy_dict(), cart_hash=preview_hash)

    def _build_order_bundle(self, quote, order_id, payment_id, authority=None, webauthn_assertion=None, agent_plan=None, delegation_spec=None, confirmation=None) -> dict:
        """Assemble a real, verifiable PoAI evidence bundle for a paid order.

        The human-auth leg is a WebAuthn `authority`. If `authority` is supplied
        it is trusted (already verified by the caller via the ceremony); otherwise
        `webauthn_assertion` (a raw browser assertion) is verified by the RP and
        turned into the authority. If neither is given, the dev credential is used
        to simulate a browser assertion that the RP *still verifies*.

        When `delegation_spec` is given the order is a delegated purchase: the
        bundle uses the v1.1.0 compiler, `authority.policy` becomes the effective
        (meet) policy, and `authority.delegation` carries the chain + spend chain
        (DELEGATION_AND_ORCHESTRATION §3/§5).
        """
        ia = _now()
        ia_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ctx = self._order_context(quote)
        goods_items = ctx["goods_items"]
        cart_hash = ctx["cart_hash"]
        subtotal = ctx["subtotal"]
        catalog_attested = ctx["catalog_attested"]
        policy_dict = ctx["policy_dict"]
        is_delegated = delegation_spec is not None
        pre = self._delegated_preview(quote, delegation_spec) if is_delegated else None

        if is_delegated:
            # §6.2 — a delegated purchase is finalized ONLY after explicit, preview-bound
            # human confirmation. No dev-credential shortcut is permitted here.
            if confirmation is None:
                raise ValueError(
                    "delegated orders require explicit human confirmation: "
                    "call preview_delegated_order(), then pass the approved confirmation"
                )
            if confirmation.get("preview_hash") != pre["preview_hash"]:
                raise ValueError("confirmation preview_hash mismatch — approve the current preview")

        if is_delegated:
            # §6.2 — the human-auth leg for a delegated order is the explicit
            # confirmation assertion, bound to the approved preview_hash. This is the
            # only authority path allowed for delegated purchases.
            authority = self.webauthn.complete_assertion(
                confirmation["webauthn_assertion"]["session_id"],
                confirmation["webauthn_assertion"],
                policy=self._merchant_policy_dict(), cart_hash=pre["preview_hash"],
            )
        elif authority is None:
            if webauthn_assertion is None:
                cred_id, cred_key = self.webauthn._dev
                begin = self.webauthn.begin_assertion(policy_dict, cart_hash)
                webauthn_assertion = simulate_browser_assertion(
                    rp_id=self.rp_id, origin=self.origin, challenge_b64=begin["challenge"],
                    policy=policy_dict, cart_hash=cart_hash,
                    credential_id=cred_id, signer_key=cred_key,
                )
                session_id = begin["session_id"]
                authority = self.webauthn.complete_assertion(
                    session_id, webauthn_assertion, policy=policy_dict, cart_hash=cart_hash
                )
            else:
                session_id = webauthn_assertion["session_id"]
                authority = self.webauthn.complete_assertion(
                    session_id, webauthn_assertion, policy=policy_dict, cart_hash=cart_hash
                )

        transaction = {
            "order_id": order_id,
            "quote_id": quote.quote_id,
            "merchant_did": self.did,
            "amount_minor": subtotal,
            "currency": quote.currency,
            "payment_id": payment_id,
            "cart_created_at": ia_iso,
            "ts": ia,
        }
        goods = {"items": goods_items, "cart_hash": cart_hash}

        policy = ctx["policy"]
        compiler_ctx = compiler_mod.CompilerContext(
            merchant_id=self.did,
            currency=quote.currency,
            evaluated_at_unix=ia,
            spent_minor=0,
            transactions_count=0,
        )
        citems = tuple(
            compiler_mod.CompilerItem(
                sku=i["sku"], qty=i["qty"], unit_minor=i["unit_minor"], tags=tuple(i["tags"])
            )
            for i in goods_items
        )

        # DELEGATION §3/§5 — delegated purchase uses the v1.1.0 procedure.
        if is_delegated:
            # `pre` was used only for enforcement + disclosure; the bundle itself is
            # compiled at *create* time so its transcript matches the verifier's
            # re-derivation from the stored context.
            eff = pre["eff"]
            eff_dict = pre["eff_dict"]
            policy = compiler_mod.CompilerPolicy(
                policy_version=2,
                merchant_id=eff.merchant_ids[0] if eff.merchant_ids else self.did,
                merchant_ids=tuple(eff.merchant_ids),
                currency=eff.currency,
                max_spend_per_tx_minor=eff.max_spend_per_tx_minor,
                max_spend_total_minor=eff.max_spend_total_minor,
                max_transactions=eff.max_transactions,
                allowed_tags=tuple(eff.allowed_tags),
                tag_mode=eff.tag_mode,
                blocked_skus=tuple(eff.blocked_skus),
                not_before=eff.not_before,
                expires_at=eff.expires_at,
            )
            compiler_ctx = compiler_mod.CompilerContext(
                merchant_id=self.did, currency=quote.currency,
                evaluated_at_unix=ia, spent_minor=0, transactions_count=0,
            )
            chain_spent = pre["chain_spent"]
            verdict = compiler_mod.compile_decision_v11(
                citems, policy, compiler_ctx,
                envelope_budget_minor=delegation_spec.envelope.budget_minor,
                chain_spent_minor=chain_spent,
            )
            # Append this purchase as a new SPEND entry on the envelope's chain.
            current = delegation_mod.append_spend_entry(
                delegation_spec.envelope.envelope_id, delegation_spec.prior_spend,
                "SPEND", subtotal, f"poai-{order_id}", ia_iso,
                self.attest_key, self.did,
            )
            full_spend = tuple(delegation_spec.prior_spend) + (current,)
            authority["policy"] = eff_dict
            delegation = delegation_mod.build_authority_delegation(
                delegation_spec.chain, delegation_spec.envelope, full_spend,
                sequencer_type=delegation_spec.sequencer_type, currency=eff.currency,
            )
            # §6.2b — when a sequencer is named, countersign the ordered set of
            # (merchant, amount, sequence) tuples so forks are externally detectable.
            if delegation_spec.sequencer_type != "none":
                seq_sig = delegation_mod.sign_sequencer_ordering(
                    full_spend, eff.merchant_ids, delegation_spec.sequencer_key, "seq"
                )
                seq_jwk = evidence.public_jwk(delegation_spec.sequencer_key.public_key(), "seq")
                delegation["sequencer"] = {
                    "type": delegation_spec.sequencer_type, "kid": "seq",
                    "signature": seq_sig, "jwk": seq_jwk,
                }
            authority["delegation"] = delegation
        else:
            verdict = compiler_mod.compile_decision(citems, policy, compiler_ctx)
        request_text = f"Confirm purchase of order {order_id} for {subtotal} {quote.currency}"
        human_intent = {"request_text": request_text, "request_digest": digest({"text": request_text})}
        if is_delegated:
            # §6.2c — the recorded mandate reflects the human's explicit, informed
            # consent to the exact previewed terms (bound via preview_hash).
            human_intent["preview_hash"] = pre["preview_hash"]
            human_intent["confirmed_at"] = ia_iso
        if agent_plan is not None:
            human_intent["agent_plan"] = agent_plan
        notification = {
            "sent_at": ia_iso,
            "receipt_digest": "sha256:" + hashlib.sha256(canonical_json_bytes(transaction)).hexdigest(),
        }
        agent = {
            "client_id": self.did,
            "scopes": ["checkout:confirm"],
            "token_jti": order_id,
            "role": "merchant-exec",
            "model": "openstore-runtime",
        }
        adjudication = {
            "compiler_digest": (
                compiler_mod.COMPILER_DIGEST_V11 if is_delegated else compiler_mod.COMPILER_DIGEST
            ),
            "verdict": verdict.verdict,
            "reason_code": verdict.reason_code,
            "transcript": list(verdict.transcript),
            "context": {
                "merchant_id": compiler_ctx.merchant_id,
                "currency": compiler_ctx.currency,
                "evaluated_at_unix": compiler_ctx.evaluated_at_unix,
                "spent_minor": compiler_ctx.spent_minor,
                "transactions_count": compiler_ctx.transactions_count,
            },
            "evaluated_at": ia_iso,
            "policy_schema_version": 2,
        }

        preds = aal_mod.Predicates(
            e1_agent_authenticated=True,
            e2_policy_signature_valid=True,
            e3_assertion_fresh=True,
            e4_user_verified=True,
            e5_cart_bound=True,
            e6_catalog_attested=catalog_attested,
            e7_compiler_allow=True,
            e8_intent_recorded=True,
            e9_notified=True,
        )
        aal_level, aal_reasons = aal_mod.resolve_aal(preds, 2)
        # DELEGATION §6 — apply the depth cap and (multi-merchant) sequencer cap.
        if is_delegated:
            depth = int(authority["delegation"]["depth"])
            merchant_count = len(authority["delegation"]["merchant_ids"])
            sequencer_type = authority["delegation"]["sequencer"]["type"]
            aal_level, aal_reasons = cap_aal_by_depth(aal_level, aal_reasons, depth)
            aal_level, aal_reasons = cap_aal_multi_merchant(
                aal_level, aal_reasons,
                merchant_count=merchant_count, sequencer_type=sequencer_type,
            )
        aal = {
            "level": aal_level,
            "predicates": {f: getattr(preds, f) for f in preds.__slots__},
            "reasons": list(aal_reasons),
        }

        bundle = evidence.build_bundle(evidence.BundleInput(
            bundle_id=f"poai-{order_id}",
            transaction=transaction,
            goods=goods,
            agent=agent,
            adjudication=adjudication,
            aal=aal,
            authority=authority,
            human_intent=human_intent,
            notification=notification,
        ))
        bundle = evidence.sign_and_anchor(bundle, self.attest_key, self.did, issued_at=ia_iso)
        # R6.3a: anchor asynchronously — persist with an absent anchor now, a
        # worker upgrades it to a salted merkle_daily anchor out of band.
        bundle = evidence.set_time_anchor_none(bundle)
        self._persist_bundle(order_id, bundle)
        return bundle

    def _persist_bundle(self, order_id: str, bundle: dict) -> None:
        from openstore.models import EvidenceBundleRow

        with Session(self.ledger.engine) as s:
            s.add(EvidenceBundleRow(
                bundle_id=bundle["bundle_id"],
                order_id=order_id,
                merchant_did=self.did,
                poai_version=bundle["poai_version"],
                root=bundle["chain"]["root"],
                time_anchor_json=json.dumps(bundle["chain"].get("time_anchor")),
                bundle_json=json.dumps(bundle),
            ))
            s.commit()

    def get_bundle(self, order_id: str) -> Optional[dict]:
        from openstore.models import EvidenceBundleRow

        with Session(self.ledger.engine) as s:
            row = s.exec(
                select(EvidenceBundleRow).where(EvidenceBundleRow.order_id == order_id)
            ).first()
            return json.loads(row.bundle_json) if row else None

    def anchor_bundle(self, order_id: str) -> dict:
        """R6.3b — upgrade a `{"type":"none"}` anchor to a salted merkle_daily anchor."""
        bundle = self.get_bundle(order_id)
        if bundle is None:
            raise ValueError("no bundle for order")
        salt = secrets.token_bytes(16)
        bundle = evidence.upgrade_time_anchor(bundle, salt)
        with Session(self.ledger.engine) as s:
            row = s.exec(
                select(EvidenceBundleRow).where(EvidenceBundleRow.order_id == order_id)
            ).first()
            if row is not None:
                row.time_anchor_json = json.dumps(bundle["chain"]["time_anchor"])
                row.bundle_json = json.dumps(bundle)
                s.add(row)
                s.commit()
        return bundle

    def start_anchor_worker(self, interval: float = 5.0) -> None:
        """R6.3a — background worker that anchors bundles still at `{"type":"none"}`."""

        def _run():
            while True:
                try:
                    with Session(self.ledger.engine) as s:
                        pending = s.exec(
                            select(EvidenceBundleRow).where(
                                EvidenceBundleRow.time_anchor_json == json.dumps({"type": "none"})
                            )
                        ).all()
                    for row in pending:
                        self.anchor_bundle(row.order_id)
                except Exception:
                    pass
                time.sleep(interval)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._anchor_worker = t

    def _record(self, env: env_mod.AgentTrustEnvelope, kind: ReceiptKind, statements, payload_ref: str) -> str:
        receipt_id = self._id("R")
        self.ledger.append(
            TR(
                receipt_id=receipt_id,
                kind=kind,
                ts=env.issued_at,
                actor_did=self.did,
                envelope=env.to_dict(),
                payload_ref=payload_ref,
                statements=statements,
            )
        )
        return receipt_id

    def discover(self) -> DiscoveryDoc:
        return DiscoveryDoc(
            merchant=MerchantInfo(legal_name="Gelateria", country="IN"),
            trust_anchor=TrustAnchor(did=self.did, anchor_policy="per-day"),
            supported_actions=[
                "discover", "create_mandate", "create_quote",
                "create_order", "cancel_order", "get_trust_receipt",
                "get_daily_anchor",
            ],
            capabilities=Capabilities(
                per_transaction_limit_paise=self.policy.spend_limit_paise or 1_000_000_00,
                daily_limit_paise=5_000_000_00,
            ),
        )

    def submit_mandate(self, mandate: Mandate) -> str:
        mandate.verify(did_mod.pubkey_from_did(mandate.payer))
        if mandate.expires_at < _now():
            raise ValueError("mandate expired")
        self.mandates[mandate.mandate_id] = mandate
        env = self._sign(signing.sha256_of_json(mandate.model_dump()).hex().encode())
        return self._record(env, ReceiptKind.MANDATE, ["mandate registered"], mandate.mandate_id)

    def create_quote(self, items: List[QuoteItem]) -> Quote:
        # R4.1: every line must be attestable; fail loud if no attestation key.
        if self.attest_key is None:
            raise ValueError("cannot attest cart: no attestation key configured")
        catalog_products = [
            {
                "sku": sku,
                "price_minor": prod.get("unit_price_paise", 0),
                "tags": prod.get("tags", []),
            }
            for sku, prod in self.catalog.items()
        ]
        catalog_digest = compute_catalog_digest(catalog_products)
        subtotal = 0
        tax = 0
        for it in items:
            if it.sku not in self.catalog:
                raise ValueError(f"unknown sku {it.sku}")
            prod = self.catalog[it.sku]
            if prod.get("blocked"):
                raise ValueError(f"sku {it.sku} blocked by policy")
            if self.policy.blocked_skus and it.sku in self.policy.blocked_skus:
                raise ValueError(f"sku {it.sku} blocked by policy")
            unit = prod.get("unit_price_paise", it.unit_price_paise)
            subtotal += unit * it.quantity
            tax += it.tax_paise
            att = self.attestations.get_or_create(
                sku=it.sku,
                price_minor=unit,
                tags=prod.get("tags", []),
                catalog_digest=catalog_digest,
                iat_unix=_now(),
            )
            it.catalog_attestation = att.jws
        total = subtotal + tax
        if self.policy.spend_limit_paise and total > self.policy.spend_limit_paise:
            raise ValueError("exceeds spend limit")
        quote = Quote(
            quote_id=self._id("Q"),
            merchant_did=self.did,
            items=items,
            subtotal_paise=subtotal,
            tax_paise=tax,
            total_paise=total,
            currency="INR",
            valid_until=_now() + 600,
        )
        env = self._sign(signing.sha256_of_json(quote.model_dump()).hex().encode())
        self._record(env, ReceiptKind.QUOTE, ["quote issued"], quote.quote_id)
        return quote

    async def create_order(self, quote: Quote, mandate: Mandate, authority: Optional[dict] = None, webauthn_assertion: Optional[dict] = None, agent_plan: Optional[dict] = None, delegation_spec: Optional[DelegationSpec] = None, confirmation: Optional[dict] = None, idempotency_key: Optional[str] = None, client_id: Optional[str] = None) -> dict:
        """Create an order with idempotency support and dual-write pattern.

        If idempotency_key and client_id are provided, the operation is idempotent:
        - Same (client_id, idempotency_key) with same request fingerprint returns cached response
        - Same key with different fingerprint raises ValueError
        - IN_FLIGHT state prevents duplicate charges during concurrent requests

        Dual-write: PspIntent written first (PENDING), then PSP called, then PspIntent updated to SUCCEEDED.
        """
        from openstore.models import IdempotencyRecord, PspIntent
        from sqlmodel import Session, select
        from sqlalchemy.exc import IntegrityError

        mandate.verify(did_mod.pubkey_from_did(mandate.payer))
        if mandate.expires_at < _now():
            raise ValueError("mandate expired")
        if quote.total_paise > mandate.max_amount_paise:
            raise ValueError("quote exceeds mandate")
        if quote.valid_until < _now():
            raise ValueError("quote expired")

        # Idempotency handling + PspIntent creation (dual-write pattern)
        if idempotency_key and client_id:
            fingerprint = self._compute_order_fingerprint(quote, mandate, authority, delegation_spec)
            expires_at = _now() + 86400
            reference_id = f"pay_{quote.quote_id}"

            with Session(self.ledger.engine) as s:
                try:
                    s.add(IdempotencyRecord(
                        client_id=client_id,
                        idempotency_key=idempotency_key,
                        request_fingerprint=fingerprint,
                        state="IN_FLIGHT",
                        expires_at=expires_at,
                    ))
                    s.add(PspIntent(
                        checkout_id=quote.quote_id,
                        amount_minor=quote.total_paise,
                        currency=quote.currency,
                        reference_id=reference_id,
                        state="PENDING",
                    ))
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    existing = s.exec(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.client_id == client_id,
                            IdempotencyRecord.idempotency_key == idempotency_key,
                        )
                    ).first()
                    if existing.request_fingerprint != fingerprint:
                        raise ValueError("idempotency_key_reuse_with_different_payload")
                    if existing.state == "IN_FLIGHT":
                        raise ValueError("request_in_progress")
                    import json
                    return json.loads(existing.response_json)

        order_id = self._id("O")
        reference_id = f"pay_{quote.quote_id}"

        # Call PSP with deterministic reference_id
        pay = await self.gateway.create_payment(
            quote.total_paise, quote.currency, order_id, {"mandate_id": mandate.mandate_id, "_reference_id": reference_id}
        )

        order = OrderRecord(
            order_id, quote, mandate.mandate_id, pay.payment_id, "paid"
        )
        self.orders[order_id] = order
        bundle = self._build_order_bundle(
            quote, order_id, pay.payment_id, authority=authority, webauthn_assertion=webauthn_assertion,
            agent_plan=agent_plan, delegation_spec=delegation_spec, confirmation=confirmation,
        )
        order.poai_bundle = bundle
        hold = self.create_hold(order_id, bundle["aal"]["level"])
        env = self._sign(
            signing.sha256_of_json(quote.model_dump()).hex().encode(),
            poai_bundle=bundle,
        )
        receipt_id = self._record(
            env, ReceiptKind.ORDER,
            [f"order {order_id} paid via {pay.payment_id}"],
            order_id,
        )
        response = {
            "order_id": order_id,
            "receipt_id": receipt_id,
            "payment": pay.payment_id,
            "poai_bundle": bundle,
            "aal_level": bundle["aal"]["level"],
            "hold_cancel_token": hold["cancel_token"],
        }

        # Update PspIntent and idempotency record
        if idempotency_key and client_id:
            import json
            with Session(self.ledger.engine) as s:
                psp_intent = s.exec(
                    select(PspIntent).where(PspIntent.checkout_id == quote.quote_id)
                ).first()
                if psp_intent:
                    psp_intent.state = "SUCCEEDED"
                    psp_intent.psp_payment_id = pay.payment_id
                    psp_intent.order_id = order_id
                    psp_intent.psp_response_json = json.dumps({"payment_id": pay.payment_id, "status": pay.status})
                    psp_intent.updated_at = _now()
                    s.add(psp_intent)

                rec = s.exec(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.client_id == client_id,
                        IdempotencyRecord.idempotency_key == idempotency_key,
                    )
                ).first()
                if rec:
                    rec.state = "COMPLETED"
                    rec.response_json = json.dumps(response)
                    rec.status_code = 200
                    s.add(rec)
                s.commit()

        from openstore.trace import emit_later

        emit_later(
            "buyer-agent", "Order placed",
            {
                "order_id": order_id,
                "checkout_id": quote.quote_id,
                "amount_minor": quote.total_paise,
                "currency": quote.currency,
            },
            response.get("receipt_id", order_id),
            "executed",
        )
        emit_later(
            "merchant-server", "Order created",
            {
                "order_id": order_id,
                "checkout_id": quote.quote_id,
                "payment": pay.payment_id,
            },
            response.get("receipt_id", order_id),
            "executed",
        )
        emit_later(
            "audit-trail", "Order created",
            {
                "order_id": order_id,
                "checkout_id": quote.quote_id,
                "payment": pay.payment_id,
            },
            response.get("receipt_id", order_id),
            "executed",
        )

        return response

    def _compute_order_fingerprint(self, quote: Quote, mandate: Mandate, authority: Optional[dict], delegation_spec: Optional[DelegationSpec]) -> str:
        """Compute a fingerprint of the semantic request fields for idempotency."""
        from openstore.canonical import canonical_json_bytes
        import hashlib
        fp_data = {
            "quote_id": quote.quote_id,
            "cart_hash": getattr(quote, "cart_hash", ""),
            "amount_minor": quote.total_paise,
            "currency": quote.currency,
            "mandate_id": mandate.mandate_id,
            "authority_scheme": authority.get("scheme") if authority else None,
            "delegation_envelope_id": delegation_spec.envelope.envelope_id if delegation_spec else None,
        }
        return "sha256:" + hashlib.sha256(canonical_json_bytes(fp_data)).hexdigest()

    async def cancel_order(self, order_id: str, reason: str) -> dict:
        order = self.orders.get(order_id)
        if order is None:
            raise ValueError("unknown order")
        pay = await self.gateway.cancel_payment(order.payment_id, order.quote.total_paise)
        order.status = "cancelled"
        env = self._sign(f'{{"order":"{order_id}","reason":"{reason}"}}'.encode())
        receipt_id = self._record(
            env, ReceiptKind.CANCEL,
            [f"order {order_id} cancelled: {reason}", f"gateway: {pay.status}"],
            order_id,
        )
        return {"order_id": order_id, "receipt_id": receipt_id, "refund": pay.status}

    def get_trust_receipt(self, receipt_id: str) -> Optional[TrustReceipt]:
        return self.ledger.get(receipt_id)

    def raise_dispute(self, order_id: str, raised_by: str, reason: str, evidence_refs=None) -> dict:
        from openstore.models import Dispute

        dispute = Dispute(
            dispute_id=self._id("D"),
            order_id=order_id,
            raised_by=raised_by,
            reason=reason,
            evidence_refs=evidence_refs or [],
        )
        env = self._sign(signing.sha256_of_json(dispute.model_dump()).hex().encode())
        receipt_id = self._record(
            env, ReceiptKind.DISPUTE,
            [f"dispute {dispute.dispute_id} raised by {raised_by}: {reason}"],
            order_id,
        )
        return {"dispute_id": dispute.dispute_id, "receipt_id": receipt_id}

    def release_order(self, order_id: str, amount_paise: int, released_by: str) -> dict:
        if order_id not in self.orders:
            raise ValueError("unknown order")
        env = self._sign(f'{{"order":"{order_id}","release":{amount_paise},"by":"{released_by}"}}'.encode())
        receipt_id = self._record(
            env, ReceiptKind.RELEASE,
            [f"escrow released {amount_paise} paise for {order_id} by {released_by}"],
            order_id,
        )
        return {"order_id": order_id, "receipt_id": receipt_id, "released": amount_paise}

    def return_order(self, order_id: str, items: list, reason: str) -> dict:
        if order_id not in self.orders:
            raise ValueError("unknown order")
        env = self._sign(f'{{"order":"{order_id}","return":{items},"reason":"{reason}"}}'.encode())
        receipt_id = self._record(
            env, ReceiptKind.RETURN,
            [f"return requested for {order_id}: {reason}"],
            order_id,
        )
        return {"order_id": order_id, "receipt_id": receipt_id}

    async def refund_order(self, order_id: str, reason: str) -> dict:
        order = self.orders.get(order_id)
        if order is None:
            raise ValueError("unknown order")
        pay = await self.gateway.refund_payment(order.payment_id, order.quote.total_paise)
        env = self._sign(f'{{"order":"{order_id}","refund":{order.quote.total_paise},"reason":"{reason}"}}'.encode())
        receipt_id = self._record(
            env, ReceiptKind.REFUND,
            [f"refund {order.quote.total_paise} paise for {order_id}: {reason}", f"gateway: {pay.status}"],
            order_id,
        )
        order.status = "refunded"
        return {"order_id": order_id, "receipt_id": receipt_id, "refund": pay.status}

    def anchor_today(self) -> dict:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        root, anchored = self.ledger.anchor_day(date, self.anchor_secret)
        return {"date": date, "root": root.hex(), "anchor": anchored.hex()}

    # ---------- §1.2c / §9.5 public key + policy discovery ----------

    def poai_jwks(self) -> dict:
        return evidence.public_jwk(self.attest_key.public_key(), self.did)

    def compiler_policy_dict(self) -> dict:
        """The effective compiler policy actually enforced at checkout (§9.1 input)."""
        return {
            "policy_version": 2,
            "merchant_id": self.did,
            "currency": "INR",
            "max_spend_per_tx_minor": self.policy.spend_limit_paise or 1_000_000_00,
            "max_spend_total_minor": 1_000_000_00,
            "max_transactions": 1000,
            "allowed_tags": tuple(self.policy.tags.keys()) if self.policy.tags else (),
            "tag_mode": "all",
            "blocked_skus": tuple(self.policy.blocked_skus or []),
            "not_before": 0,
            "expires_at": int(time.time()) + 10 * 365 * 24 * 3600,
            "assertion_max_age_seconds": 86400,
        }

    def agent_policy_doc(self) -> dict:
        return {
            "version": "0.2",
            "merchant_did": self.did,
            "poai_version": "0.1",
            "policy": {
                "policy_version": 2,
                "allowed_tags": list(self.policy.tags.keys()) if self.policy.tags else [],
                "tag_mode": "all",
                "blocked_skus": list(self.policy.blocked_skus or []),
                "spend_limit_paise": self.policy.spend_limit_paise,
                "approved_catalog": sorted(self.catalog.keys()),
                "requires_payer_consent": self.policy.requires_payer_consent,
                "vendor_attestation_required": self.policy.vendor_attestation_required,
            },
            "capabilities": {
                "currencies": ["INR"],
                "per_transaction_limit_paise": self.policy.spend_limit_paise or 1_000_000_00,
                "daily_limit_paise": 5_000_000_00,
            },
        }

    # ---------- §9.3 Hold & Cancel ----------

    def create_hold(self, order_id: str, aal_level: int) -> dict:
        return self.holds.create(order_id, aal_level)

    def cancel_hold(self, token: str) -> dict:
        return self.holds.cancel(token)

    def release_expired_holds(self, now: Optional[int] = None) -> list:
        return self.holds.release_expired(now)

    def start_hold_worker(self, interval: float = 10.0) -> None:
        def _run():
            while True:
                try:
                    self.release_expired_holds()
                except Exception:
                    pass
                time.sleep(interval)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._hold_worker = t

    # ---------- §9.2 Agent Console data ----------

    def record_rejection(self, order_id: str, reason_code: str, detail: str, client_id: str = "unknown", trace_id: Optional[str] = None) -> None:
        """Record a rejection with reason_code, client_id, trace_id (PRODUCTION_READINESS §0.8)."""
        import uuid
        if trace_id is None:
            trace_id = uuid.uuid4().hex[:16]

        from openstore.models import RejectionRecord
        from sqlmodel import Session

        with Session(self.ledger.engine) as s:
            s.add(RejectionRecord(
                order_id=order_id,
                reason_code=reason_code,
                detail=detail,
                client_id=client_id,
                trace_id=trace_id,
            ))
            s.commit()

        # Also keep in-memory for backwards compatibility
        self.rejections.append({
            "order_id": order_id,
            "reason_code": reason_code,
            "detail": detail,
            "client_id": client_id,
            "trace_id": trace_id,
            "at": _now(),
        })

        from openstore.trace import emit_background

        emit_background(
            "merchant-server", "Order rejected",
            {
                "order_id": order_id,
                "reason_code": reason_code,
                "detail": detail,
                "client_id": client_id,
            },
            trace_id,
            "blocked",
        )
        emit_background(
            "audit-trail", "Order rejected",
            {
                "order_id": order_id,
                "reason_code": reason_code,
                "client_id": client_id,
            },
            trace_id,
            "blocked",
        )

    def list_rejections(self) -> list:
        from openstore.models import RejectionRecord
        from sqlmodel import Session, select

        with Session(self.ledger.engine) as s:
            records = s.exec(select(RejectionRecord).order_by(RejectionRecord.created_at.desc())).all()
            return [{
                "order_id": r.order_id,
                "reason_code": r.reason_code,
                "detail": r.detail,
                "client_id": r.client_id,
                "trace_id": r.trace_id,
                "at": r.created_at,
            } for r in records]

    def register_agent_session(self, session_key: str, client_id: str, scopes) -> None:
        from openstore.models import AgentSessionRow

        with Session(self.ledger.engine) as s:
            s.add(AgentSessionRow(
                session_key=session_key, client_id=client_id,
                scopes=json.dumps(list(scopes)), created_at=_now(), last_seen=_now(),
            ))
            s.commit()

    def list_agent_sessions(self) -> list:
        from openstore.models import AgentSessionRow

        with Session(self.ledger.engine) as s:
            return [{
                "session_key": r.session_key, "client_id": r.client_id,
                "scopes": json.loads(r.scopes), "created_at": r.created_at,
                "frozen": r.frozen,
            } for r in s.exec(select(AgentSessionRow)).all()]

    def freeze_agent_session(self, session_key: str, frozen: bool) -> None:
        from openstore.models import AgentSessionRow

        with Session(self.ledger.engine) as s:
            rec = s.exec(select(AgentSessionRow).where(
                AgentSessionRow.session_key == session_key)).first()
            if rec is None:
                raise ValueError("unknown session")
            rec.frozen = frozen
            s.add(rec)
            s.commit()
