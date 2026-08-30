"""Pure decision procedure (IMPLEMENTATION_SPEC §3).

`compile_decision` is a deterministic function of its three frozen inputs. It
MUST NOT read a clock, a database, the network, or emit traces — the verifier
re-executes it over the bundle's embedded context and byte-compares the
transcript, so any hidden input would break reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .canonical import digest

REASON_CODES: Tuple[str, ...] = (
    "currency_mismatch",
    "merchant_mismatch",
    "policy_not_yet_valid",
    "policy_expired",
    "tx_count_exceeded",
    "qty_invalid",
    "sku_blocked",
    "sku_duplicate",
    "tag_violation",
    "spend_per_tx_exceeded",
    "spend_cumulative_exceeded",
)

COMPILER_SPEC = {
    "compiler_version": "1.0.0",
    "check_order": [
        "currency_match",
        "merchant_lock",
        "policy_not_before",
        "policy_expiry",
        "transaction_count",
        "item_qty",
        "item_blocked_sku",
        "item_tag_allowlist",
        "spend_per_tx",
        "spend_cumulative",
    ],
    "reason_codes": list(REASON_CODES),
    "tag_modes": ["all", "any"],
    "item_iteration_order": "sku_ascending",
    "money_unit": "minor_integer",
    "empty_allowed_tags_semantics": "unconstrained",
}
COMPILER_DIGEST = digest(COMPILER_SPEC)

# DELEGATION_AND_ORCHESTRATION §5.1a — v1.1.0 adds the `spend_envelope` check at
# position 10 and moves `spend_cumulative` to 11; `merchant_lock` becomes a
# set-membership test against `merchant_ids`. The v1.0.0 digest is retired (no
# bundle has yet been issued against it in production).
COMPILER_VERSION_V11 = "1.1.0"
REASON_CODES_V11: Tuple[str, ...] = REASON_CODES + ("spend_envelope_exceeded",)
COMPILER_SPEC_V11 = {
    "compiler_version": "1.1.0",
    "check_order": [
        "currency_match",
        "merchant_lock",
        "policy_not_before",
        "policy_expiry",
        "transaction_count",
        "item_qty",
        "item_blocked_sku",
        "item_tag_allowlist",
        "spend_per_tx",
        "spend_envelope",
        "spend_cumulative",
    ],
    "reason_codes": list(REASON_CODES_V11),
    "tag_modes": ["all", "any"],
    "item_iteration_order": "sku_ascending",
    "money_unit": "minor_integer",
    "empty_allowed_tags_semantics": "unconstrained",
}
COMPILER_DIGEST_V11 = "sha256:10230521796f94d9039a8f25d0c03c0e48f870b53ad54885001a3a440f9f7ed9"


class LegacyPolicyError(ValueError):
    """Raised when a v1 (legacy) policy is passed to compile_decision."""


@dataclass(frozen=True, slots=True)
class CompilerItem:
    sku: str
    qty: int
    unit_minor: int
    tags: Tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompilerPolicy:
    policy_version: int
    merchant_id: str
    currency: str
    max_spend_per_tx_minor: int
    max_spend_total_minor: int
    max_transactions: int
    allowed_tags: Tuple[str, ...]
    tag_mode: str
    blocked_skus: Tuple[str, ...]
    not_before: int
    expires_at: int
    assertion_max_age_seconds: int = 86400
    step_up_above_minor: int = 0  # PRODUCTION_READINESS §3.2 — amount above which a fresh per-tx WebAuthn assertion is required
    merchant_ids: Tuple[str, ...] = ()  # v1.1.0 — set membership for merchant_lock


@dataclass(frozen=True, slots=True)
class CompilerContext:
    merchant_id: str
    currency: str
    evaluated_at_unix: int
    spent_minor: int
    transactions_count: int


@dataclass(frozen=True, slots=True)
class CompilerVerdict:
    verdict: str
    reason_code: Optional[str]
    transcript: Tuple[dict, ...]


def _find_duplicate(items: Tuple[CompilerItem, ...]) -> Optional[CompilerItem]:
    seen: set[str] = set()
    for i in items:
        if i.sku in seen:
            return i
        seen.add(i.sku)
    return None


def _deny(reason_code: str, transcript: list[dict]) -> CompilerVerdict:
    return CompilerVerdict("DENY", reason_code, tuple(transcript))


def compile_decision(
    items: Tuple[CompilerItem, ...],
    policy: CompilerPolicy,
    context: CompilerContext,
) -> CompilerVerdict:
    return _compile_core(items, policy, context, v11=False)


def compile_decision_v11(
    items: Tuple[CompilerItem, ...],
    policy: CompilerPolicy,
    context: CompilerContext,
    *,
    envelope_budget_minor: int,
    chain_spent_minor: int = 0,
) -> CompilerVerdict:
    """DELEGATION §5.1 — v1.1.0 procedure with the `spend_envelope` check."""
    return _compile_core(
        items, policy, context, v11=True,
        envelope_budget_minor=envelope_budget_minor, chain_spent_minor=chain_spent_minor,
    )


def _compile_core(
    items: Tuple[CompilerItem, ...],
    policy: CompilerPolicy,
    context: CompilerContext,
    *,
    v11: bool,
    envelope_budget_minor: int = 0,
    chain_spent_minor: int = 0,
) -> CompilerVerdict:
    if policy.policy_version != 2:
        raise LegacyPolicyError(
            f"compile_decision requires policy_version == 2, got {policy.policy_version}"
        )
    if not items:
        raise ValueError("empty_cart")

    items = tuple(sorted(items, key=lambda i: i.sku))
    transcript: list[dict] = []

    # 1. currency_match
    if not (policy.currency == context.currency == "INR"):
        transcript.append({
            "check": "currency_match", "result": "fail",
            "policy_currency": policy.currency, "context_currency": context.currency,
        })
        return _deny("currency_mismatch", transcript)
    transcript.append({
        "check": "currency_match", "result": "pass",
        "policy_currency": policy.currency, "context_currency": context.currency,
    })

    # 2. merchant_lock (v1.1.0: set membership against merchant_ids)
    if v11:
        ids = policy.merchant_ids or (policy.merchant_id,)
        if context.merchant_id not in ids:
            transcript.append({
                "check": "merchant_lock", "result": "fail",
                "expected": list(ids), "actual": context.merchant_id,
            })
            return _deny("merchant_mismatch", transcript)
    else:
        if policy.merchant_id != context.merchant_id:
            transcript.append({
                "check": "merchant_lock", "result": "fail",
                "expected": policy.merchant_id, "actual": context.merchant_id,
            })
            return _deny("merchant_mismatch", transcript)
    transcript.append({
        "check": "merchant_lock", "result": "pass",
        "expected": list(policy.merchant_ids) if v11 else policy.merchant_id,
        "actual": context.merchant_id,
    })

    # 3. policy_not_before
    if context.evaluated_at_unix < policy.not_before:
        transcript.append({
            "check": "policy_not_before", "result": "fail",
            "not_before": policy.not_before, "evaluated_at": context.evaluated_at_unix,
        })
        return _deny("policy_not_yet_valid", transcript)
    transcript.append({
        "check": "policy_not_before", "result": "pass",
        "not_before": policy.not_before, "evaluated_at": context.evaluated_at_unix,
    })

    # 4. policy_expiry
    if context.evaluated_at_unix >= policy.expires_at:
        transcript.append({
            "check": "policy_expiry", "result": "fail",
            "expires_at": policy.expires_at, "evaluated_at": context.evaluated_at_unix,
        })
        return _deny("policy_expired", transcript)
    transcript.append({
        "check": "policy_expiry", "result": "pass",
        "expires_at": policy.expires_at, "evaluated_at": context.evaluated_at_unix,
    })

    # 5. transaction_count
    if context.transactions_count + 1 > policy.max_transactions:
        transcript.append({
            "check": "transaction_count", "result": "fail",
            "completed": context.transactions_count, "limit": policy.max_transactions,
        })
        return _deny("tx_count_exceeded", transcript)
    transcript.append({
        "check": "transaction_count", "result": "pass",
        "completed": context.transactions_count, "limit": policy.max_transactions,
    })

    # 6. item_qty (+ duplicate detection, before qty check)
    dup = _find_duplicate(items)
    if dup is not None:
        transcript.append({
            "check": "item_qty", "result": "fail",
            "sku": dup.sku, "qty": dup.qty,
        })
        return _deny("sku_duplicate", transcript)
    for i in items:
        if not (isinstance(i.qty, int) and i.qty >= 1):
            transcript.append({
                "check": "item_qty", "result": "fail",
                "sku": i.sku, "qty": i.qty,
            })
            return _deny("qty_invalid", transcript)
        transcript.append({"check": "item_qty", "result": "pass", "sku": i.sku, "qty": i.qty})

    # 7. item_blocked_sku
    blocked = set(policy.blocked_skus)
    for i in items:
        if i.sku in blocked:
            transcript.append({"check": "item_blocked_sku", "result": "fail", "sku": i.sku})
            return _deny("sku_blocked", transcript)
        transcript.append({"check": "item_blocked_sku", "result": "pass", "sku": i.sku})

    # 8. item_tag_allowlist
    allowed = set(policy.allowed_tags)
    for i in items:
        item_tags = set(i.tags)
        if allowed:
            if policy.tag_mode == "all":
                ok = item_tags <= allowed
            else:  # "any"
                ok = bool(item_tags & allowed)
        else:
            ok = True  # empty allowlist => unconstrained
        if not ok:
            transcript.append({
                "check": "item_tag_allowlist", "result": "fail",
                "sku": i.sku, "mode": policy.tag_mode,
                "item_tags": sorted(item_tags), "allowed": sorted(allowed),
            })
            return _deny("tag_violation", transcript)
        transcript.append({
            "check": "item_tag_allowlist", "result": "pass",
            "sku": i.sku, "mode": policy.tag_mode,
            "item_tags": sorted(i.tags), "allowed": sorted(allowed),
        })

    # 9. spend_per_tx
    total = sum(i.unit_minor * i.qty for i in items)
    if total > policy.max_spend_per_tx_minor:
        transcript.append({
            "check": "spend_per_tx", "result": "fail",
            "total_minor": total, "limit_minor": policy.max_spend_per_tx_minor,
        })
        return _deny("spend_per_tx_exceeded", transcript)
    transcript.append({
        "check": "spend_per_tx", "result": "pass",
        "total_minor": total, "limit_minor": policy.max_spend_per_tx_minor,
    })

    # 10. spend_envelope (v1.1.0 only) — chain_state.spent + total <= envelope budget
    if v11:
        if chain_spent_minor + total > envelope_budget_minor:
            transcript.append({
                "check": "spend_envelope", "result": "fail",
                "chain_spent_minor": chain_spent_minor, "total_minor": total,
                "limit_minor": envelope_budget_minor,
            })
            return _deny("spend_envelope_exceeded", transcript)
        transcript.append({
            "check": "spend_envelope", "result": "pass",
            "chain_spent_minor": chain_spent_minor, "total_minor": total,
            "limit_minor": envelope_budget_minor,
        })

    # 11 (v1.1.0) / 10 (v1.0.0). spend_cumulative — checks the root budget
    if context.spent_minor + total > policy.max_spend_total_minor:
        transcript.append({
            "check": "spend_cumulative", "result": "fail",
            "spent_minor": context.spent_minor, "total_minor": total,
            "limit_minor": policy.max_spend_total_minor,
        })
        return _deny("spend_cumulative_exceeded", transcript)
    transcript.append({
        "check": "spend_cumulative", "result": "pass",
        "spent_minor": context.spent_minor, "total_minor": total,
        "limit_minor": policy.max_spend_total_minor,
    })

    return CompilerVerdict("ALLOW", None, tuple(transcript))
