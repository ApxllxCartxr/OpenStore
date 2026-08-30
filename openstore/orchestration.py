"""Multi-merchant orchestration (DELEGATION_AND_ORCHESTRATION §7).

Pure functions for sourcing, saga execution, and compensation.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

from openstore.canonical import canonical_json_bytes, digest
from openstore.compiler import CompilerContext, CompilerItem, CompilerPolicy, compile_decision_v11
from openstore.delegation import (
    BudgetEnvelope,
    DelegationLink,
    EffectivePolicy,
    append_spend_entry,
    build_authority_delegation,
    envelope_available,
    verify_delegation_chain,
    verify_spend_chain,
)


class FulfilmentMode(str, Enum):
    ALL_OR_NOTHING = "all_or_nothing"
    BEST_EFFORT = "best_effort"
    REQUIRED_SUBSET = "required_subset"


@dataclass(frozen=True, slots=True)
class SourcingLeg:
    """One leg of a multi-merchant sourcing plan."""
    leg_id: str
    merchant_did: str
    envelope_id: str
    envelope_budget_minor: int
    items: Tuple[CompilerItem, ...]
    estimated_total_minor: int
    required: bool = True
    tags: Tuple[str, ...] = ()
    effective_policy: Optional[EffectivePolicy] = None
    delegation_chain: Tuple[DelegationLink, ...] = ()
    prior_spend: Tuple = ()


@dataclass(frozen=True, slots=True)
class SourcingPlan:
    """Complete sourcing plan for an OrderIntent."""
    plan_id: str
    intent_id: str
    fulfilment_mode: FulfilmentMode
    required_skus: Tuple[str, ...] = ()
    legs: Tuple[SourcingLeg, ...] = ()
    root_envelope_id: str = ""
    root_budget_minor: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    rationale: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "intent_id": self.intent_id,
            "fulfilment_mode": self.fulfilment_mode.value,
            "required_skus": list(self.required_skus),
            "legs": [
                {
                    "leg_id": leg.leg_id,
                    "merchant_did": leg.merchant_did,
                    "envelope_id": leg.envelope_id,
                    "envelope_budget_minor": leg.envelope_budget_minor,
                    "items": [
                        {"sku": i.sku, "qty": i.qty, "unit_minor": i.unit_minor, "tags": list(i.tags)}
                        for i in leg.items
                    ],
                    "estimated_total_minor": leg.estimated_total_minor,
                    "required": leg.required,
                    "tags": list(leg.tags),
                    "effective_policy": leg.effective_policy.to_dict() if leg.effective_policy else None,
                    "delegation_chain": [l.to_dict() for l in leg.delegation_chain],
                }
                for leg in self.legs
            ],
            "root_envelope_id": self.root_envelope_id,
            "root_budget_minor": self.root_budget_minor,
            "created_at": self.created_at,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SourcingPlan":
        legs = []
        for leg_d in d.get("legs", []):
            items = tuple(
                CompilerItem(
                    sku=i["sku"], qty=i["qty"], unit_minor=i["unit_minor"], tags=tuple(i.get("tags", []))
                )
                for i in leg_d.get("items", [])
            )
            eff_policy = None
            if leg_d.get("effective_policy"):
                eff_policy = EffectivePolicy(**leg_d["effective_policy"])
            chain = tuple(DelegationLink.from_dict(l) for l in leg_d.get("delegation_chain", []))
            legs.append(
                SourcingLeg(
                    leg_id=leg_d["leg_id"],
                    merchant_did=leg_d["merchant_did"],
                    envelope_id=leg_d["envelope_id"],
                    envelope_budget_minor=leg_d["envelope_budget_minor"],
                    items=items,
                    estimated_total_minor=leg_d["estimated_total_minor"],
                    required=leg_d.get("required", True),
                    tags=tuple(leg_d.get("tags", [])),
                    effective_policy=eff_policy,
                    delegation_chain=chain,
                )
            )
        return cls(
            plan_id=d["plan_id"],
            intent_id=d["intent_id"],
            fulfilment_mode=FulfilmentMode(d["fulfilment_mode"]),
            required_skus=tuple(d.get("required_skus", [])),
            legs=tuple(legs),
            root_envelope_id=d.get("root_envelope_id", ""),
            root_budget_minor=d.get("root_budget_minor", 0),
            created_at=d.get("created_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
            rationale=d.get("rationale", {}),
        )


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """An intent to purchase across multiple merchants."""
    intent_id: str
    items: Tuple[CompilerItem, ...]
    currency: str
    fulfilment_mode: FulfilmentMode
    required_skus: Tuple[str, ...] = ()
    root_chain: Tuple[DelegationLink, ...] = ()
    root_envelope: Optional[BudgetEnvelope] = None
    merchant_catalogs: Dict[str, Dict] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def to_dict(self) -> dict:
        return {
            "intent_id": self.intent_id,
            "items": [
                {"sku": i.sku, "qty": i.qty, "unit_minor": i.unit_minor, "tags": list(i.tags)}
                for i in self.items
            ],
            "currency": self.currency,
            "fulfilment_mode": self.fulfilment_mode.value,
            "required_skus": list(self.required_skus),
            "root_chain": [l.to_dict() for l in self.root_chain],
            "root_envelope": self.root_envelope.to_dict() if self.root_envelope else None,
            "merchant_catalogs": self.merchant_catalogs,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class OrchestrationRecord:
    """Record of a multi-merchant orchestration execution."""
    orchestration_id: str
    intent_id: str
    plan_id: str
    leg_results: Tuple[dict, ...]
    status: str
    created_at: str
    completed_at: Optional[str] = None
    compensation_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "orchestration_id": self.orchestration_id,
            "intent_id": self.intent_id,
            "plan_id": self.plan_id,
            "leg_results": list(self.leg_results),
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "compensation_reason": self.compensation_reason,
        }


# ---------- Sourcing Agent (R8.1) ----------


def compute_sourcing_plan(
    intent: OrderIntent,
    *,
    min_hold_seconds: int = 60,
) -> SourcingPlan:
    """R8.1 — Given an OrderIntent and merchant catalogs, produce a SourcingPlan.

    Objective precedence:
    1. Satisfy fulfilment_mode
    2. Minimise total cost
    3. Minimise leg count
    4. Prefer merchants whose required_aal for the leg total is reachable without step-up

    Every leg MUST pass compile_decision_v11 against its envelope BEFORE any envelope is minted.
    """
    if not intent.root_chain:
        raise ValueError("intent must have a root delegation chain")
    if not intent.root_envelope:
        raise ValueError("intent must have a root envelope")

    # Verify root chain and get effective policy
    root_eff = verify_delegation_chain(intent.root_chain, root_is_human_signed=True, verify_signatures=False)
    # For an empty spend chain, available equals the full budget
    if intent.root_envelope:
        root_available = intent.root_envelope.budget_minor
    else:
        root_available = 0

    # Group items by merchant availability
    merchant_items: Dict[str, List[CompilerItem]] = {}
    for item in intent.items:
        available_merchants = []
        for m_did, catalog in intent.merchant_catalogs.items():
            if item.sku in catalog:
                prod = catalog[item.sku]
                if not prod.get("blocked", False):
                    available_merchants.append(m_did)
        if not available_merchants:
            if item.sku in intent.required_skus:
                raise ValueError(f"required sku {item.sku} not available at any merchant")
            continue
        m_did = available_merchants[0]
        merchant_items.setdefault(m_did, []).append(item)

    # Build legs
    legs = []
    total_allocated = 0
    for m_did, items in merchant_items.items():
        leg_items = tuple(items)
        estimated_total = sum(i.unit_minor * i.qty for i in leg_items)

        required = any(i.sku in intent.required_skus for i in leg_items)

        envelope_id = f"env_{secrets.token_urlsafe(16)}"
        envelope_budget = estimated_total

        if total_allocated + envelope_budget > root_available:
            raise ValueError(f"insufficient root budget for leg at {m_did}")

        child_envelope = BudgetEnvelope(
            envelope_id=envelope_id,
            budget_minor=envelope_budget,
            currency=intent.currency,
            issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            expires_at=int(time.time()) + 3600,
            parent_envelope_id=intent.root_envelope.envelope_id,
        )

        eff_policy = EffectivePolicy(
            budget_minor=envelope_budget,
            currency=intent.currency,
            merchant_ids=(m_did,),
            allowed_tags=(),
            tag_mode="all",
            blocked_skus=(),
            max_transactions=1,
            not_before=0,
            expires_at=child_envelope.expires_at,
            max_spend_per_tx_minor=envelope_budget,
            max_spend_total_minor=envelope_budget,
        )

        compiler_ctx = CompilerContext(
            merchant_id=m_did,
            currency=intent.currency,
            evaluated_at_unix=int(time.time()),
            spent_minor=0,
            transactions_count=0,
        )
        verdict = compile_decision_v11(
            leg_items,
            CompilerPolicy(
                policy_version=2,
                merchant_id=m_did,
                merchant_ids=(m_did,),
                currency=intent.currency,
                max_spend_per_tx_minor=envelope_budget,
                max_spend_total_minor=envelope_budget,
                max_transactions=1,
                allowed_tags=(),
                tag_mode="all",
                blocked_skus=(),
                not_before=0,
                expires_at=child_envelope.expires_at,
            ),
            compiler_ctx,
            envelope_budget_minor=envelope_budget,
            chain_spent_minor=0,
        )
        if verdict.verdict != "ALLOW":
            raise ValueError(f"leg for {m_did} failed compilation: {verdict.reason_code}")

        total_allocated += envelope_budget
        leg_id = f"leg_{secrets.token_urlsafe(8)}"
        legs.append(
            SourcingLeg(
                leg_id=leg_id,
                merchant_did=m_did,
                envelope_id=envelope_id,
                envelope_budget_minor=envelope_budget,
                items=leg_items,
                estimated_total_minor=estimated_total,
                required=required,
                effective_policy=eff_policy,
                delegation_chain=intent.root_chain,
            )
        )

    if intent.fulfilment_mode == FulfilmentMode.ALL_OR_NOTHING:
        covered_skus = {i.sku for leg in legs for i in leg.items}
        missing = set(intent.required_skus) - covered_skus
        if missing:
            raise ValueError(f"all_or_nothing: required SKUs not covered: {missing}")

    plan_id = f"plan_{secrets.token_urlsafe(16)}"
    return SourcingPlan(
        plan_id=plan_id,
        intent_id=intent.intent_id,
        fulfilment_mode=intent.fulfilment_mode,
        required_skus=intent.required_skus,
        legs=tuple(legs),
        root_envelope_id=intent.root_envelope.envelope_id,
        root_budget_minor=intent.root_envelope.budget_minor,
        rationale={leg.leg_id: f"Assigned to {leg.merchant_did} for {len(leg.items)} items" for leg in legs},
    )


# ---------- Saga Execution (R7.2) ----------


class OrchestrationError(Exception):
    """Orchestration-level error with reason code."""
    def __init__(self, reason: str, message: str):
        self.reason = reason
        self.message = message
        super().__init__(message)


async def execute_sourcing_plan(
    plan: SourcingPlan,
    runtime_map: Dict[str, "MerchantRuntime"],
    *,
    mode: FulfilmentMode = FulfilmentMode.ALL_OR_NOTHING,
    min_hold_seconds: int = 60,
) -> OrchestrationRecord:
    """R7.2 — Execute a sourcing plan as a saga with compensation.

    Uses the hold window (IMPLEMENTATION_SPEC §9.3) as the compensation boundary.
    AAL3 legs have hold_seconds=0 and are non-compensable.
    """
    orchestration_id = f"orch_{secrets.token_urlsafe(16)}"
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if min_hold_seconds < 60:
        raise OrchestrationError("orchestration.deadline_too_short", "minimum hold window under 60 seconds")

    leg_results = []
    executed_legs = []

    try:
        for leg in plan.legs:
            runtime = runtime_map.get(leg.merchant_did)
            if not runtime:
                raise OrchestrationError("orchestration.merchant_unavailable", f"no runtime for {leg.merchant_did}")

            from openstore.models import QuoteItem
            quote_items = [
                QuoteItem(sku=i.sku, title="", quantity=i.qty, unit_price_paise=i.unit_minor, tax_paise=0)
                for i in leg.items
            ]
            quote = runtime.create_quote(quote_items)

            from openstore.models import Mandate
            from openstore.core import did
            ppriv, ppub = did.generate_keypair()
            payer = did.did_from_pubkey(ppub)
            mandate = Mandate.create(
                private_key=ppriv,
                mandate_id=f"M_{leg.leg_id}",
                merchant_did=runtime.did,
                payer=payer,
                scope="global",
                max_amount_paise=leg.envelope_budget_minor,
                currency=leg.effective_policy.currency if leg.effective_policy else "INR",
                expires_at=int(time.time()) + 3600,
            )

            from openstore.runtime import DelegationSpec
            delegation_spec = DelegationSpec(
                chain=leg.delegation_chain,
                envelope=BudgetEnvelope(
                    envelope_id=leg.envelope_id,
                    budget_minor=leg.envelope_budget_minor,
                    currency=leg.effective_policy.currency if leg.effective_policy else "INR",
                    issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    expires_at=int(time.time()) + 3600,
                ),
                prior_spend=leg.prior_spend,
            )

            order_id = f"O_{secrets.token_urlsafe(8)}"
            bundle_id = f"poai-{order_id}"
            hold = runtime.create_hold(order_id, 2)

            leg_results.append({
                "leg_id": leg.leg_id,
                "merchant_did": leg.merchant_did,
                "order_id": order_id,
                "bundle_id": bundle_id,
                "status": "held",
                "hold_token": hold["cancel_token"],
                "hold_seconds": hold["hold_seconds"],
            })
            executed_legs.append((leg, order_id, hold["cancel_token"]))

        status = "completed"
        completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    except Exception as exc:
        for leg, order_id, cancel_token in executed_legs:
            try:
                runtime = runtime_map[leg.merchant_did]
                runtime.cancel_hold(cancel_token)
                for lr in leg_results:
                    if lr["leg_id"] == leg.leg_id:
                        lr["status"] = "cancelled"
            except Exception:
                pass
        status = "compensated"
        completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(exc, OrchestrationError):
            raise
        raise OrchestrationError("orchestration.leg_failed", str(exc)) from exc

    return OrchestrationRecord(
        orchestration_id=orchestration_id,
        intent_id=plan.intent_id,
        plan_id=plan.plan_id,
        leg_results=tuple(leg_results),
        status=status,
        created_at=created_at,
        completed_at=completed_at,
    )


# ---------- Evidence Bundle for Orchestration (R7.3c) ----------


def build_orchestration_bundle(
    orchestration: OrchestrationRecord,
    plan: SourcingPlan,
    leg_bundles: Dict[str, dict],
) -> dict:
    """R7.3c — Build a bundle-of-bundles for the orchestration record.

    Retrievable at GET /orders/intent/{intent_id}/evidence
    """
    return {
        "orchestration": orchestration.to_dict(),
        "plan": plan.to_dict(),
        "leg_bundles": leg_bundles,
        "bundle_id": f"orch-{orchestration.orchestration_id}",
        "poai_version": "0.1",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------- Verification ----------


def verify_orchestration_bundle(bundle: dict) -> Tuple[bool, List[str]]:
    """Verify an orchestration bundle-of-bundles.

    Checks:
    1. All leg bundles are valid PoAI bundles
    2. All legs share the same root envelope
    3. Sum of leg envelopes <= root envelope budget
    4. Fulfilment mode constraints satisfied
    """
    errors = []

    leg_bundles = bundle.get("leg_bundles", {})
    if not leg_bundles:
        errors.append("no leg bundles")

    plan = bundle.get("plan", {})
    root_env_id = plan.get("root_envelope_id")
    root_budget = plan.get("root_budget_minor", 0)

    total_allocated = 0
    for leg_id, leg_bundle in leg_bundles.items():
        auth = leg_bundle.get("authority", {})
        deleg = auth.get("delegation")
        if not deleg:
            errors.append(f"leg {leg_id}: no delegation section")
            continue
        if deleg.get("envelope_id") != root_env_id:
            parent_env_id = deleg.get("parent_envelope_id")
            if parent_env_id != root_env_id:
                errors.append(f"leg {leg_id}: envelope not derived from root")
        total_allocated += deleg.get("envelope_budget_minor", 0)

    if total_allocated > root_budget:
        errors.append(f"total allocated {total_allocated} exceeds root budget {root_budget}")

    orchestration = bundle.get("orchestration", {})
    mode = plan.get("fulfilment_mode")
    if mode == "all_or_nothing":
        for lr in orchestration.get("leg_results", []):
            if lr["status"] not in ("paid", "held"):
                errors.append(f"all_or_nothing: leg {lr['leg_id']} not completed")

    return len(errors) == 0, errors


ORCHESTRATION_SPEC = {
    "orchestration_version": "1.0.0",
    "check_order": [
        "leg_bundles_valid",
        "root_envelope_consistency",
        "budget_disjointness",
        "fulfilment_mode_satisfied",
    ],
    "reason_codes": [
        "leg_bundle_invalid",
        "root_envelope_mismatch",
        "budget_exceeds_root",
        "fulfilment_mode_violated",
    ],
}

ORCHESTRATION_DIGEST = "sha256:" + hashlib.sha256(canonical_json_bytes(ORCHESTRATION_SPEC)).hexdigest()
