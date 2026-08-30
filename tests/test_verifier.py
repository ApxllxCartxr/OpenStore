import ast
import importlib.util
import json
from pathlib import Path

import cbor2
import hashlib
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from openstore.aal import Predicates, resolve_aal
from openstore.canonical import canonical_json_bytes, digest
from openstore.compiler import (
    COMPILER_DIGEST,
    CompilerContext,
    CompilerItem,
    CompilerPolicy,
    compile_decision,
)
from openstore.evidence import BundleInput, b64url, b64url_decode, build_bundle, compute_chain, sign_and_anchor
from openstore.verify import verify_bundle

from reference.merchant import poai_keys


def _sign_jws(payload, key, kid):
    h = b64url(canonical_json_bytes({"alg": "ES256", "kid": kid}))
    p = b64url(canonical_json_bytes(payload))
    der = key.sign(f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{h}.{p}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _mint_authority(auth_key, policy, policy_hash, cart_hash=None):
    nums = auth_key.public_key().public_numbers()
    cose = {1: 2, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
    cose_b64 = b64url(cbor2.dumps(cose))
    if cart_hash:
        ph = bytes.fromhex(policy_hash.replace("sha256:", ""))
        ch = bytes.fromhex(cart_hash.replace("sha256:", ""))
        challenge = b64url(hashlib.sha256(ph + ch).digest())
        cb = {"mode": "cart", "policy_hash": policy_hash, "cart_hash": cart_hash}
    else:
        challenge = b64url(bytes.fromhex(policy_hash.replace("sha256:", "")))
        cb = {"mode": "policy", "policy_hash": policy_hash}
    client_data = {"type": "webauthn.get", "challenge": challenge, "origin": "https://localhost"}
    client_data_b64 = b64url(json.dumps(client_data, separators=(",", ":")).encode())
    auth_data = bytes(32) + bytes([0x04]) + (0).to_bytes(4, "big")  # UV flag set
    signed = auth_data + hashlib.sha256(b64url_decode(client_data_b64)).digest()
    der = auth_key.sign(signed, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return {
        "policy": policy,
        "policy_hash": policy_hash,
        "webauthn": {
            "credential_id": b64url(b"cred1"),
            "client_data_json": client_data_b64,
            "authenticator_data": b64url(auth_data),
            "signature": b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big")),
            "uv": True,
            "sign_count": 0,
            "signed_at": "2026-08-27T09:14:22Z",
            "challenge_binding": cb,
        },
        "enrolment": {
            "public_key": cose_b64,
            "aaguid": b64url(bytes(16)),
            "attestation_format": "none",
            "enrolled_at": "2026-08-27T09:00:00Z",
        },
    }


def _mint_bundle():
    mkey = poai_keys.signing_key()
    kid = poai_keys.kid()
    jwks = poai_keys.jwks()

    policy = {
        "policy_version": 2, "merchant_id": "gelateria-roma", "currency": "INR",
        "max_spend_per_tx_minor": 50000, "max_spend_total_minor": 200000,
        "max_transactions": 10, "allowed_tags": ["dairy-free", "vegan"],
        "tag_mode": "all", "blocked_skus": [], "not_before": 1787000000,
        "expires_at": 1788000000, "assertion_max_age_seconds": 86400,
    }
    policy_hash = digest(policy)
    items = (
        CompilerItem("GEL-VAN-500", 2, 21000, ("dairy-free", "vegan")),
    )
    ctx = CompilerContext("gelateria-roma", "INR", 1787000900, 0, 0)
    verdict = compile_decision(items, CompilerPolicy(
        policy_version=policy["policy_version"], merchant_id=policy["merchant_id"],
        currency=policy["currency"], max_spend_per_tx_minor=policy["max_spend_per_tx_minor"],
        max_spend_total_minor=policy["max_spend_total_minor"],
        max_transactions=policy["max_transactions"], allowed_tags=tuple(policy["allowed_tags"]),
        tag_mode=policy["tag_mode"], blocked_skus=tuple(policy["blocked_skus"]),
        not_before=policy["not_before"], expires_at=policy["expires_at"],
    ), ctx)

    goods_items = [{
        "sku": "GEL-VAN-500", "qty": 2, "unit_minor": 21000,
        "tags": ["dairy-free", "vegan"],
        "catalog_attestation": _sign_jws(
            {"sku": "GEL-VAN-500", "price_minor": 21000,
             "tags": ["dairy-free", "vegan"], "catalog_digest": "sha256:aa",
             "merchant_id": "gelateria-roma", "iat": 1787000000}, mkey, kid),
    }]
    auth_key = ec.generate_private_key(ec.SECP256R1())
    authority = _mint_authority(auth_key, policy, policy_hash)
    transaction = {
        "merchant_id": "gelateria-roma", "checkout_id": "chk-1",
        "cart_created_at": "2026-08-27T09:14:22Z", "amount_minor": 42000,
        "currency": "INR", "psp": {"provider": "razorpay", "order_id": "o1",
        "payment_link_id": "p1"},
    }
    agent = {"client_id": "client", "display_name": "Buyer", "token_jti": "jti",
             "scopes": ["checkout:confirm"], "consent_granted_at": "2026-08-27T09:14:00Z"}
    human_intent = {"request_digest": digest({"text": "buy ice cream"}),
                    "request_text": "buy ice cream", "captured_at": "2026-08-27T09:14:10Z",
                    "channel": "web", "channel_message_id": "m1"}
    notification = {"sent_at": "2026-08-27T09:14:30Z", "channel": "discord",
                    "receipt_digest": digest({"channel": "discord", "message_id": "m1",
                                              "sent_at": "2026-08-27T09:14:30Z"})}
    adjudication = {
        "compiler_version": "1.0.0", "compiler_digest": COMPILER_DIGEST,
        "policy_schema_version": 2, "evaluated_at": "2026-08-27T09:14:22Z",
        "context": {"spent_minor": 0, "transactions_count": 0, "currency": "INR",
                    "merchant_id": "gelateria-roma", "evaluated_at_unix": 1787000900},
        "verdict": verdict.verdict, "reason_code": verdict.reason_code,
        "transcript": [dict(t) for t in verdict.transcript],
    }

    from openstore.aal import Predicates
    preds = Predicates(e1_agent_authenticated=True, e2_policy_signature_valid=True,
                       e3_assertion_fresh=True, e4_user_verified=True, e5_cart_bound=False,
                       e6_catalog_attested=True, e7_compiler_allow=True,
                       e8_intent_recorded=True, e9_notified=True)
    level, reasons = resolve_aal(preds, 2)
    aal = {"level": level, "predicates": {
        "e1_agent_authenticated": True, "e2_policy_signature_valid": True,
        "e3_assertion_fresh": True, "e4_user_verified": True, "e5_cart_bound": False,
        "e6_catalog_attested": True, "e7_compiler_allow": True,
        "e8_intent_recorded": True, "e9_notified": True,
    }, "reasons": list(reasons)}

    bundle = build_bundle(BundleInput(
        bundle_id="poai_" + "A" * 26, transaction=transaction, goods={
            "cart_hash": "sha256:aa", "cart_version": 1, "items": goods_items},
        agent=agent, adjudication=adjudication, aal=aal,
        authority=authority, human_intent=human_intent, notification=notification))
    sign_and_anchor(bundle, mkey, kid, "2026-08-27T09:15:04Z")
    return bundle, jwks


def test_verify_full_bundle_passes():
    bundle, jwks = _mint_bundle()
    res = verify_bundle(bundle, jwks)
    assert res.ok, res.failures
    assert res.exit_code == 0


def _rechain(bundle):
    """Recompute chain + merchant signature after mutating a claim, so the
    bundle stays internally consistent (chain valid) but the *claim* is wrong
    — this exercises the verifier's dishonesty checks, not tamper detection."""
    mkey = poai_keys.signing_key()
    kid = poai_keys.kid()
    sections = {n: bundle.get(n) for n in (
        "transaction", "human_intent", "authority", "goods",
        "agent", "adjudication", "notification", "aal")}
    bundle["chain"] = compute_chain(sections)
    sign_and_anchor(bundle, mkey, kid, "2026-08-27T09:15:04Z")


def test_verify_amount_mismatch_fails():
    bundle, jwks = _mint_bundle()
    bundle["transaction"]["amount_minor"] = 99999
    _rechain(bundle)
    res = verify_bundle(bundle, jwks)
    assert not res.ok
    assert "amount_consistency" in res.failures


def test_verify_transcript_mismatch_fails():
    bundle, jwks = _mint_bundle()
    bundle["adjudication"]["transcript"][0]["result"] = "fail"
    _rechain(bundle)
    res = verify_bundle(bundle, jwks)
    assert not res.ok
    assert "re_execution" in res.failures


def test_verify_chain_broken_fails_exit2():
    # no rechain -> tamper detected by chain integrity (exit 2)
    bundle, jwks = _mint_bundle()
    bundle["goods"]["items"][0]["sku"] = "TAMPERED"
    res = verify_bundle(bundle, jwks)
    assert not res.ok
    assert res.exit_code == 2
    assert "chain_integrity" in res.failures


def test_verify_unsupported_compiler_digest_exit3():
    bundle, jwks = _mint_bundle()
    bundle["adjudication"]["compiler_digest"] = "sha256:deadbeef"
    _rechain(bundle)
    res = verify_bundle(bundle, jwks)
    assert res.exit_code == 3
    assert "compiler_digest" in res.failures


def test_verify_no_jwks_warns_but_runs():
    bundle, _ = _mint_bundle()
    res = verify_bundle(bundle, None)
    # without jwks the catalog attestation cannot be verified -> e6 false,
    # but the run completes and reports a warning rather than crashing
    assert "merchant_signature_unverified_no_jwks" in res.warnings


def test_verifier_imports_nothing_from_merchant():
    path = Path(importlib.util.find_spec("openstore.verify").origin)
    tree = ast.parse(path.read_text())
    forbidden = {"merchant", "fastapi", "sqlmodel", "razorpay"}
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.Import):
            mod = node.names[0].name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
        if mod in forbidden:
            raise AssertionError(f"verifier imports forbidden module: {mod}")
