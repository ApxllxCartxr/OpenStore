"""Standalone-style PoAI verifier (IMPLEMENTATION_SPEC §7).

Isolation: imports only openstore.* pure modules + cryptography/cbor2. Never
imports merchant / fastapi / sqlmodel / razorpay / network. The same
compiler, AAL, and chain logic the issuer uses is re-run here so a third party
can verify a bundle without trusting the merchant.
"""

from __future__ import annotations

import base64
import cbor2
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from openstore import compiler
from openstore.aal import Predicates, resolve_aal
from openstore.canonical import canonical_json_bytes, digest
from openstore.compiler import (
    CompilerContext,
    CompilerItem,
    CompilerPolicy,
    LegacyPolicyError,
    compile_decision,
)
from openstore.core.authority import (
    MAX_AAL_BY_DEPTH,
    cap_aal_by_depth,
    cap_aal_multi_merchant,
)
from openstore.delegation import (
    DELEGATION_DIGEST,
    detect_forks,
    SPENDCHAIN_DIGEST,
    BudgetEnvelope,
    DelegationLink,
    SpendEntry,
    verify_delegation_chain,
    verify_spend_chain,
    verify_sequencer_ordering,
)
from openstore.evidence import b64url, b64url_decode, verify_chain, verify_merchant_signature


# ---------- small helpers ----------


def _rfc3339_to_unix(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _jwks_to_key(jwks):
    if not jwks:
        return None
    keys = jwks.get("keys", [jwks]) if isinstance(jwks, dict) else []
    if not keys:
        return None
    k = keys[0]
    x = b64url_decode(k["x"])
    y = b64url_decode(k["y"])
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
    ).public_key()


def _verify_es256_jws(jws: str, public_key) -> Optional[dict]:
    try:
        h, p, sig_b64 = jws.split(".")
    except ValueError:
        return None
    # R1.2b: allowlist alg == ES256 only; reject "none" and anything else.
    try:
        header = json.loads(b64url_decode(h))
    except Exception:
        return None
    if header.get("alg") != "ES256":
        return None
    signing_input = f"{h}.{p}".encode("ascii")
    raw = b64url_decode(sig_b64)
    if len(raw) != 64:
        return None
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    try:
        public_key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception:
        return None
    try:
        return json.loads(b64url_decode(p))
    except Exception:
        return None


# Single source of truth for COSE->EC decoding lives in the shared, cycle-free
# verifier module (openstore.verify imports openstore.core.authority, so the
# helpers must not back-import this package).
from openstore.core.authority.webauthn_verify import cose_public_key_to_ec  # noqa: E402


# ---------- individual checks (return (ok, code_or_none)) ----------


def _check_schema(bundle: dict):
    required = {"poai_version", "bundle_id", "transaction", "human_intent",
                "authority", "goods", "agent", "adjudication", "notification", "aal", "chain"}
    if not required.issubset(bundle.keys()):
        return False, "schema_invalid"
    if bundle.get("poai_version") != "0.1":
        return False, "schema_invalid"
    chain = bundle.get("chain") or {}
    if not isinstance(chain.get("links"), list) or len(chain["links"]) != 8:
        return False, "schema_invalid"
    if "root" not in chain or "merchant_signature" not in chain:
        return False, "schema_invalid"
    return True, None


# The three WebAuthn authority checks live in the shared, cycle-free verifier
# module so native_webauthn.verify and the bundle verifier share one implementation.
from openstore.core.authority.webauthn_verify import (  # noqa: E402
    _verify_challenge_binding,
    _verify_uv,
    _verify_webauthn_assertion,
)


def _verify_catalog_attestations(bundle: dict, merchant_key):
    if merchant_key is None:
        return False, "attestation_invalid"
    for it in bundle["goods"]["items"]:
        payload = _verify_es256_jws(it["catalog_attestation"], merchant_key)
        if payload is None:
            return False, "attestation_invalid"
        if (payload.get("sku") != it["sku"]
                or payload.get("price_minor") != it["unit_minor"]
                or sorted(payload.get("tags", [])) != sorted(it.get("tags", []))):
            return False, "attestation_item_mismatch"
        if payload.get("iat", 0) > _rfc3339_to_unix(bundle["transaction"]["cart_created_at"]):
            return False, "attestation_stale"
    return True, None


def _rerun_compiler(bundle, digest_version="1.0.0"):
    goods = bundle["goods"]
    adj = bundle["adjudication"]
    auth = bundle.get("authority") or {}
    p = auth.get("policy") or {}
    items = tuple(
        CompilerItem(sku=i["sku"], qty=i["qty"], unit_minor=i["unit_minor"],
                     tags=tuple(i.get("tags", [])))
        for i in goods["items"]
    )
    policy = CompilerPolicy(
        policy_version=p.get("policy_version", 2),
        merchant_id=p.get("merchant_id", ""),
        currency=p.get("currency", "INR"),
        max_spend_per_tx_minor=p.get("max_spend_per_tx_minor", 0),
        max_spend_total_minor=p.get("max_spend_total_minor", 0),
        max_transactions=p.get("max_transactions", 0),
        allowed_tags=tuple(p.get("allowed_tags", [])),
        tag_mode=p.get("tag_mode", "all"),
        blocked_skus=tuple(p.get("blocked_skus", [])),
        not_before=p.get("not_before", 0),
        expires_at=p.get("expires_at", 0),
        merchant_ids=tuple(p.get("merchant_ids", [])),
    )
    ctx = adj["context"]
    context = CompilerContext(
        merchant_id=ctx["merchant_id"], currency=ctx["currency"],
        evaluated_at_unix=ctx["evaluated_at_unix"], spent_minor=ctx["spent_minor"],
        transactions_count=ctx["transactions_count"],
    )
    if digest_version == "1.1.0":
        deleg = auth.get("delegation") or {}
        envelope_budget_minor = deleg.get("envelope_budget_minor", 0)
        chain_spent_minor = 0
        chain = deleg.get("spend_chain")
        if chain:
            try:
                chain_spent_minor = sum(
                    e.get("amount_minor", 0) for e in chain if e.get("entry_type") == "SPEND"
                )
            except Exception:
                chain_spent_minor = 0
        # The bundle's spend chain already includes THIS purchase's SPEND entry,
        # but the compiler re-adds `total` when checking the envelope cap. Subtract
        # the current txn amount so chain_spent_minor means "spent before this txn".
        total = sum(i.get("unit_minor", 0) * i.get("qty", 0) for i in goods["items"])
        chain_spent_minor = max(0, chain_spent_minor - total)
        try:
            return compiler.compile_decision_v11(
                items, policy, context,
                envelope_budget_minor=envelope_budget_minor,
                chain_spent_minor=chain_spent_minor,
            )
        except compiler.LegacyPolicyError:
            return None
    try:
        return compile_decision(items, policy, context)
    except LegacyPolicyError:
        return None


def _compute_predicates(bundle, *, e2, e4, e5, e6, e7):
    ag = bundle.get("agent") or {}
    scopes = ag.get("scopes") or []
    e1 = bool(ag.get("client_id") and scopes and ag.get("token_jti")
              and "checkout:confirm" in scopes)
    auth = bundle.get("authority") or {}
    wa = auth.get("webauthn") or {}
    e3 = False
    if wa and auth.get("policy"):
        try:
            age = _rfc3339_to_unix(wa["signed_at"]) if isinstance(wa.get("signed_at"), str) else wa["signed_at"]
            ev = _rfc3339_to_unix(bundle["adjudication"]["evaluated_at"]) if isinstance(bundle["adjudication"]["evaluated_at"], str) else bundle["adjudication"]["evaluated_at"]
            max_age = auth["policy"].get("assertion_max_age_seconds", 0)
            e3 = (ev - age) <= max_age
        except Exception:
            e3 = False
    hi = bundle.get("human_intent")
    e8 = False
    if hi:
        e8 = digest({"text": hi["request_text"]}) == hi.get("request_digest")
    ntf = bundle.get("notification")
    e9 = bool(ntf and ntf.get("sent_at") and isinstance(ntf.get("receipt_digest"), str)
              and ntf["receipt_digest"].startswith("sha256:"))
    return Predicates(
        e1_agent_authenticated=e1,
        e2_policy_signature_valid=e2,
        e3_assertion_fresh=e3,
        e4_user_verified=e4,
        e5_cart_bound=e5,
        e6_catalog_attested=e6,
        e7_compiler_allow=e7,
        e8_intent_recorded=e8,
        e9_notified=e9,
    )


def _verify_delegation_section(deleg: dict, merchant_key) -> Optional[str]:
    """Verify the `authority.delegation` section (R5.3). Returns a reason string
    on failure, or None on success."""
    if deleg.get("delegation_digest") != DELEGATION_DIGEST:
        return "delegation_digest_mismatch"
    if deleg.get("spendchain_digest") != SPENDCHAIN_DIGEST:
        return "spendchain_digest_mismatch"
    try:
        links = tuple(DelegationLink.from_dict(d) for d in deleg["chain"])
        verify_delegation_chain(links, root_is_human_signed=True, verify_signatures=False)
    except (ValueError, KeyError) as e:
        return f"delegation_chain_invalid:{e}"
    try:
        env = BudgetEnvelope.from_dict({
            "envelope_id": deleg["envelope_id"],
            "budget_minor": deleg["envelope_budget_minor"],
            "currency": deleg.get("currency", "inr"),
            "issued_at": deleg.get("issued_at", ""),
            "expires_at": deleg.get("expires_at", 0),
            "parent_envelope_id": deleg.get("parent_envelope_id"),
        })
        entries = tuple(SpendEntry.from_dict(e) for e in deleg.get("spend_chain", []))
        if entries:
            # SPEND entries are merchant-countersigned (verifiable with the
            # merchant JWKS); holder-signed entries are checked structurally via
            # the chain links offline. verify_signatures=False => chain-integrity only.
            keys = {"merchant": merchant_key} if merchant_key is not None else None
            verify_spend_chain(entries, env, keys=keys, verify_signatures=False)
    except (ValueError, KeyError) as e:
        return f"spend_chain_invalid:{e}"
    # §6.2b — a named sequencer must have countersigned the ordered
    # (merchant, amount, sequence) set; otherwise the ledger is unverifiable.
    seq = deleg.get("sequencer") or {}
    if seq.get("type") not in (None, "none"):
        if "signature" not in seq or "jwk" not in seq:
            return "sequencer_signature_missing"
        try:
            pub = _jwks_to_key(seq["jwk"])
            if pub is None:
                return "sequencer_signature_missing"
            verify_sequencer_ordering(
                tuple(SpendEntry.from_dict(e) for e in deleg.get("spend_chain", [])),
                tuple(deleg.get("merchant_ids", [])),
                seq["signature"], pub,
            )
        except (ValueError, KeyError, Exception) as e:  # noqa: BLE001
            return f"sequencer_signature_invalid:{e}"
    return None


# ---------- top-level verify ----------


@dataclass
class VerifyResult:
    ok: bool
    exit_code: int
    poai_version: str = ""
    bundle_id: str = ""
    checks: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    re_derived: dict = field(default_factory=dict)
    claimed: dict = field(default_factory=dict)
    merchant_asserted: list = field(default_factory=list)
    liability_note: str = ""


def verify_bundle(bundle: dict, jwks=None) -> VerifyResult:
    poai_version = bundle.get("poai_version", "")
    bundle_id = bundle.get("bundle_id", "")
    merchant_key = _jwks_to_key(jwks)
    checks: list = []
    warnings: list = []
    failures: list = []

    def add(name, result, detail=""):
        checks.append({"name": name, "result": result, "detail": detail})
        if result == "fail":
            failures.append(name)
        elif result == "warn":
            warnings.append(name)

    # 1. schema
    ok, code = _check_schema(bundle)
    if not ok:
        add("schema", "fail", code)
        return _finalize(checks, warnings, failures, poai_version, bundle_id, exit_code=2)

    # 2. chain_integrity
    try:
        verify_chain(bundle)
        add("chain_integrity", "pass", f"8 links, root {bundle['chain']['root'][:19]}…")
    except ValueError as e:
        add("chain_integrity", "fail", str(e))
        return _finalize(checks, warnings, failures, poai_version, bundle_id, exit_code=2)

    # 3. merchant_signature
    if merchant_key is None:
        add("merchant_signature", "warn", "no jwks provided")
        warnings.append("merchant_signature_unverified_no_jwks")
    else:
        try:
            verify_merchant_signature(
                bundle["chain"]["merchant_signature"], merchant_key, bundle["chain"]["root"])
            add("merchant_signature", "pass")
        except ValueError:
            add("merchant_signature", "fail", "merchant_signature_invalid")
            return _finalize(checks, warnings, failures, poai_version, bundle_id, exit_code=1)

    # 4. time_anchor
    ta = bundle.get("chain", {}).get("time_anchor")
    if ta is None or (isinstance(ta, dict) and ta.get("type") == "none"):
        add("time_anchor", "pass", "absent")
        warnings.append("time_anchor_absent")
    elif isinstance(ta, dict) and ta.get("type") in ("rekor", "merkle_daily"):
        add("time_anchor", "pass", ta.get("type"))
    else:
        add("time_anchor", "fail", "time_anchor_malformed")
        return _finalize(checks, warnings, failures, poai_version, bundle_id, exit_code=1)

    # 5-7. authority (webauthn / challenge / uv)
    auth = bundle.get("authority")
    e2 = e4 = e5 = False
    if auth and auth.get("webauthn"):
        ok, code = _verify_webauthn_assertion(auth)
        e2 = ok
        add("webauthn_assertion", "pass" if ok else "fail", code or "")
        if not ok:
            failures.append("webauthn_assertion")
        ok, code = _verify_challenge_binding(auth)
        add("challenge_binding", "pass" if ok else "fail", code or "")
        if not ok:
            failures.append("challenge_binding")
        ok, code = _verify_uv(auth)
        e4 = ok
        add("uv_flag", "pass" if ok else "fail", code or "")
        if not ok:
            failures.append("uv_flag")
        cb = auth["webauthn"].get("challenge_binding") or {}
        # For delegated orders (§6.2) the human's confirmation is bound to the
        # preview_hash (which embeds the cart), not the raw cart_hash.
        is_deleg = bool((bundle.get("authority") or {}).get("delegation"))
        cart_ok = cb.get("cart_hash") == bundle["goods"].get("cart_hash")
        preview_ok = is_deleg and cb.get("cart_hash") == (bundle.get("human_intent") or {}).get("preview_hash")
        e5 = cb.get("mode") == "cart" and (cart_ok or preview_ok)
    else:
        add("webauthn_assertion", "fail", "webauthn_signature_invalid")
        failures.append("webauthn_assertion")

    # 8. catalog_attestations
    ok, code = _verify_catalog_attestations(bundle, merchant_key)
    e6 = ok
    add("catalog_attestations", "pass" if ok else "fail", code or "")
    if not ok:
        failures.append("catalog_attestations")

    # 9. compiler_digest — accept v1.0.0 and v1.1.0 (R3.6b dispatch).
    digest_in = bundle["adjudication"].get("compiler_digest")
    if digest_in == compiler.COMPILER_DIGEST:
        digest_version = "1.0.0"
    elif digest_in == compiler.COMPILER_DIGEST_V11:
        digest_version = "1.1.0"
    else:
        add("compiler_digest", "fail", "unsupported_compiler_digest")
        return _finalize(checks, warnings, failures, poai_version, bundle_id, exit_code=3)
    add("compiler_digest", "pass")

    # 10. re_execution
    reexec = _rerun_compiler(bundle, digest_version)
    e7 = False
    if reexec is None:
        add("re_execution", "pass", "legacy policy (v1) — not re-adjudicated")
    else:
        claimed_verdict = bundle["adjudication"]["verdict"]
        claimed_transcript = bundle["adjudication"]["transcript"]
        if reexec.verdict != claimed_verdict:
            add("re_execution", "fail", "verdict_mismatch")
            failures.append("re_execution")
        elif canonical_json_bytes(list(reexec.transcript)) != canonical_json_bytes(claimed_transcript):
            add("re_execution", "fail", "transcript_mismatch")
            failures.append("re_execution")
        else:
            add("re_execution", "pass")
            e7 = True

    # 11. amount_consistency
    total = sum(i["unit_minor"] * i["qty"] for i in bundle["goods"]["items"])
    if total != bundle["transaction"]["amount_minor"]:
        add("amount_consistency", "fail", "amount_mismatch")
        failures.append("amount_consistency")
    else:
        add("amount_consistency", "pass")

    # 12. aal
    preds = _compute_predicates(bundle, e2=e2, e4=e4, e5=e5, e6=e6, e7=e7)
    level, reasons = resolve_aal(preds, bundle["adjudication"].get("policy_schema_version", 2))
    # DELEGATION §6 — apply the depth cap and (if present) the unsequenced
    # multi-merchant envelope cap. Non-delegated bundles have depth 0 (cap 3),
    # so this is a no-op for them.
    depth = 0
    merchant_count = 1
    sequencer_type = "none"
    deleg = (bundle.get("authority") or {}).get("delegation")
    if deleg:
        depth = int(deleg.get("depth", 0))
        merchant_count = len(deleg.get("merchant_ids", []) or [])
        sequencer_type = (deleg.get("sequencer") or {}).get("type", "none")
    level, reasons = cap_aal_by_depth(level, reasons, depth)
    level, reasons = cap_aal_multi_merchant(
        level, reasons, merchant_count=merchant_count, sequencer_type=sequencer_type
    )
    if level != bundle["aal"]["level"] or list(reasons) != list(bundle["aal"]["reasons"]):
        add("aal", "fail", "aal_mismatch")
        failures.append("aal")
    else:
        add("aal", "pass")

    # 13/14. delegation chain + spend chain (DELEGATION §3 / R5.3, when present)
    deleg = (bundle.get("authority") or {}).get("delegation")
    if deleg:
        derr = _verify_delegation_section(deleg, merchant_key)
        if derr:
            add("delegation", "fail", derr)
            failures.append("delegation")
        else:
            add("delegation", "pass")

    result = _finalize(checks, warnings, failures, poai_version, bundle_id, exit_code=0)
    result.re_derived = {
        "verdict": reexec.verdict if reexec else bundle["adjudication"]["verdict"],
        "reason_code": reexec.reason_code if reexec else bundle["adjudication"]["reason_code"],
        "aal_level": level,
        "aal_reasons": list(reasons),
    }
    result.claimed = {
        "verdict": bundle["adjudication"]["verdict"],
        "aal_level": bundle["aal"]["level"],
    }
    # R5.1c: agent_plan is an unsigned claim — label it merchant_asserted.
    hi = bundle.get("human_intent") or {}
    if hi.get("agent_plan") is not None:
        result.merchant_asserted.append("human_intent.agent_plan")
    result.merchant_asserted.extend([
        "adjudication.context.spent_minor",
        "adjudication.context.transactions_count",
    ])
    result.liability_note = "Proposed liability position (not a network rule): SHARED / ISSUER"
    return result


def scan_bundles_for_forks(bundles: list) -> list:
    """Batch cross-bundle fork detection (DELEGATION §4). Per-bundle `verify_bundle`
    cannot catch a fork — it only becomes visible when two bundles claiming the same
    (envelope_id, sequence) are compared. Run this over a set of bundles (e.g. all
    receipts for an envelope) to surface `fork_proof`s."""
    return detect_forks(bundles)


def _finalize(checks, warnings, failures, poai_version, bundle_id, exit_code):
    return VerifyResult(
        ok=(len(failures) == 0),
        exit_code=exit_code if (exit_code != 0) else (1 if failures else 0),
        poai_version=poai_version,
        bundle_id=bundle_id,
        checks=checks,
        warnings=warnings,
        failures=failures,
    )


def to_json_dict(res: VerifyResult) -> dict:
    return {
        "poai_version": res.poai_version,
        "bundle_id": res.bundle_id,
        "ok": res.ok,
        "checks": res.checks,
        "warnings": res.warnings,
        "failures": res.failures,
        "re_derived": res.re_derived,
        "claimed": res.claimed,
        "merchant_asserted": res.merchant_asserted,
        "liability_note": res.liability_note,
    }
