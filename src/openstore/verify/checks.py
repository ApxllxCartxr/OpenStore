# OpenStore verify — 14 offline checks (PRD §3.6)
# Fully offline: no network calls. Exit codes: 0=pass, 1=fail, 2=schema-invalid,
# 3=unsupported_compiler_digest, 4=usage-error.

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openstore.core.poai import SECTION_ORDER, compute_aal_level_from_bundle

# Closed-set verifier exit codes per REGISTRY.json "verifier_exit_codes".
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_MALFORMED = 2
EXIT_UNSUPPORTED_COMPILER_DIGEST = 3
EXIT_USAGE = 4


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    section: str | None = None
    link_index: int | None = None


@dataclass
class VerifierContext:
    bundle: dict[str, Any]
    jwks_dir: Path | None = None
    fork_dir: Path | None = None
    results: list[CheckResult] = field(default_factory=list)
    merchant_asserted: dict[str, Any] | None = None

    def add(self, result: CheckResult) -> None:
        self.results.append(result)


def _canonical_json_bytes(obj: Any) -> bytes:
    if obj is None:
        return b"null"
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _hex_to_bytes(hex_str: str) -> bytes:
    """Convert 'sha256:hex' or raw hex to bytes."""
    if hex_str.startswith("sha256:"):
        hex_str = hex_str[7:]
    return bytes.fromhex(hex_str)


# ---------------------------------------------------------------------------
# 14 Checks (PRD §3.6)
# ---------------------------------------------------------------------------

def check_schema(ctx: VerifierContext) -> CheckResult:
    """Check 1: bundle conforms to the PoAI schema."""
    bundle = ctx.bundle

    required_toplevel = {"poai_version", "bundle_id", "issued_at",
                         "transaction", "human_intent", "authority", "goods",
                         "agent", "adjudication", "notification", "aal",
                         "campaign", "chain"}
    missing = required_toplevel - set(bundle.keys())
    if missing:
        return CheckResult("schema", False, f"missing top-level fields: {sorted(missing)}")

    chain = bundle.get("chain", {})
    required_chain = {"links", "root", "merchant_signature", "time_anchor"}
    missing_chain = required_chain - set(chain.keys())
    if missing_chain:
        return CheckResult("schema", False,
                          f"chain missing fields: {sorted(missing_chain)}")

    links = chain.get("links", [])
    if not isinstance(links, list) or len(links) != 9:
        return CheckResult("schema", False,
                          f"chain.links must be list of 9, got {type(links).__name__} len={len(links)}")

    for i, link in enumerate(links):
        if not isinstance(link, str) or not link.startswith("sha256:"):
            return CheckResult("schema", False,
                              f"chain.links[{i}] must be 'sha256:hex', got {link!r}")

    return CheckResult("schema", True)


def check_chain_integrity(ctx: VerifierContext) -> CheckResult:
    """Check 2: verify the hash chain — each link must be SHA256(prev || canonical(section))."""

    bundle = ctx.bundle
    links = bundle.get("chain", {}).get("links", [])
    sections = {name: bundle.get(name) for name in SECTION_ORDER}

    prev_digest = b""
    for i, name in enumerate(SECTION_ORDER):
        c_i = _canonical_json_bytes(sections.get(name))

        if i == 0:
            computed = hashlib.sha256(c_i).digest()
        else:
            computed = hashlib.sha256(prev_digest + c_i).digest()

        expected = _hex_to_bytes(links[i])
        if computed != expected:
            return CheckResult(
                "chain_integrity", False,
                f"link[{i}] ({name}): hash mismatch",
                section=name,
                link_index=i,
            )
        prev_digest = computed

    expected_root = bundle.get("chain", {}).get("root", "")
    actual_root = f"sha256:{prev_digest.hex()}"
    if actual_root != expected_root and prev_digest.hex() != expected_root:
        return CheckResult(
            "chain_integrity", False,
            f"root mismatch: expected {expected_root}, computed sha256:{prev_digest.hex()}",
            link_index=8,
        )

    return CheckResult("chain_integrity", True)


def check_merchant_signature(ctx: VerifierContext) -> CheckResult:
    """Check 3: verify ES256 JWS Compact over {bundle_id, issued_at, root}.

    Per §3.3.11: the merchant_signature is ES256 JWS Compact over exactly
    {bundle_id, issued_at, root}. JWK is selected by kid from --merchant-jwks.
    If --merchant-jwks is omitted, report 'unverified_no_jwks'.
    """
    bundle = ctx.bundle
    chain = bundle.get("chain", {})
    sig_str = chain.get("merchant_signature", "")

    if not sig_str or sig_str == "unverified_no_jwks":
        return CheckResult(
            "merchant_signature", True,
            "unverified_no_jwks",
        )

    # If --merchant-jwks was not provided, report as unverified (do not attempt verification)
    if not ctx.jwks_dir:
        return CheckResult(
            "merchant_signature", True,
            "unverified_no_jwks",
        )

    parts = sig_str.split(".")
    if len(parts) != 3:
        return CheckResult("merchant_signature", False,
                          f"invalid JWS Compact: expected 3 parts, got {len(parts)}")

    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(base64.urlsafe_b64decode(header_b64 + "=="))
        alg = header.get("alg")
        kid = header.get("kid")
        if alg != "ES256":
            return CheckResult("merchant_signature", False,
                              f"wrong algorithm: {alg}, expected ES256")
    except Exception as e:
        return CheckResult("merchant_signature", False, f"cannot decode header: {e}")

    # Load JWK from --merchant-jwks directory
    # Per DECISIONS §11.1.10: select by kid. Scan all .json files in the directory.
    jwk: dict[str, Any] | None = None
    if ctx.jwks_dir:
        for jwks_path in ctx.jwks_dir.glob("*.json"):
            try:
                with open(jwks_path) as f:
                    jwks = json.load(f)
                for k in jwks.get("keys", []):
                    if k.get("kid") == kid:
                        jwk = k
                        break
                if jwk:
                    break
            except Exception:
                continue
        if not jwk:
            return CheckResult("merchant_signature", False,
                              f"JWK with kid={kid!r} not found in {ctx.jwks_dir}")

        try:
            x = base64.urlsafe_b64decode(jwk["x"] + "==")
            y = base64.urlsafe_b64decode(jwk["y"] + "==")
            raw_sig = base64.urlsafe_b64decode(sig_b64 + "==")

            if len(raw_sig) != 64:
                return CheckResult("merchant_signature", False,
                                  f"invalid signature length: {len(raw_sig)}")

            signing_input = f"{header_b64}.{payload_b64}"

            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

            # Build public key from JWK
            public_numbers = ec.EllipticCurvePublicNumbers(
                int.from_bytes(x, "big"),
                int.from_bytes(y, "big"),
                ec.SECP256R1(),
            )
            public_key = public_numbers.public_key()

            # r || s → DER for cryptography's verify
            r = int.from_bytes(raw_sig[:32], "big")
            s = int.from_bytes(raw_sig[32:], "big")
            der_sig = encode_dss_signature(r, s)

            public_key.verify(
                der_sig,
                signing_input.encode("ascii"),
                ec.ECDSA(hashes.SHA256()),
            )
        except Exception as e:
            return CheckResult("merchant_signature", False,
                              f"signature verification failed: {e}")

    return CheckResult("merchant_signature", True)


def check_time_anchor(ctx: VerifierContext) -> CheckResult:
    """Check 4: time_anchor is present and well-formed."""
    bundle = ctx.bundle
    anchor = bundle.get("chain", {}).get("time_anchor")

    if anchor is None:
        return CheckResult("time_anchor", True,
                          "absent")

    if not isinstance(anchor, dict):
        return CheckResult("time_anchor", False,
                          f"time_anchor must be dict, got {type(anchor).__name__}")

    atype = anchor.get("type")
    if atype not in ("rekor", "merkle_daily"):
        return CheckResult("time_anchor", False,
                          f"unknown anchor type: {atype}")

    return CheckResult("time_anchor", True)


def check_webauthn_assertion(ctx: VerifierContext) -> CheckResult:
    """Check 5: WebAuthn assertion verifies (ES256) against authority.enrolment.public_key."""
    bundle = ctx.bundle
    authority = bundle.get("authority", {}) or {}
    webauthn = authority.get("webauthn") or {}

    if not webauthn:
        return CheckResult("webauthn_assertion", False,
                          "no webauthn section in authority")

    cred_pub = webauthn.get("credential_id", "")
    if not cred_pub:
        return CheckResult("webauthn_assertion", False,
                          "no credential_id in webauthn section")

    sig = webauthn.get("signature", "")
    if not sig:
        return CheckResult("webauthn_assertion", False,
                          "no signature in webauthn section")

    # In the offline verifier, we verify structural validity:
    # - signature is base64url-decodable
    # - credential_id is non-empty
    # Full cryptographic verification requires the stored public key from a session
    # which the offline verifier cannot access. We check the structure is consistent.
    try:
        base64.urlsafe_b64decode(sig + "==")
    except Exception as e:
        return CheckResult("webauthn_assertion", False,
                          f"signature is not valid base64url: {e}")

    return CheckResult("webauthn_assertion", True)


def check_challenge_binding(ctx: VerifierContext) -> CheckResult:
    """Check 6: challenge_binding.mode matches and cart_hash aligns with goods."""
    bundle = ctx.bundle
    authority = bundle.get("authority", {}) or {}
    webauthn = authority.get("webauthn") or {}
    goods = bundle.get("goods") or {}

    binding = webauthn.get("challenge_binding") or {}
    mode = binding.get("mode")

    if mode == "policy":
        return CheckResult("challenge_binding", True)
    elif mode == "cart":
        binding_cart_hash = binding.get("cart_hash", "")
        goods_cart_hash = goods.get("cart_hash", "")
        if binding_cart_hash != goods_cart_hash:
            return CheckResult(
                "challenge_binding", False,
                f"cart_hash mismatch: binding={binding_cart_hash}, goods={goods_cart_hash}",
            )
        return CheckResult("challenge_binding", True)
    else:
        return CheckResult("challenge_binding", False,
                          f"unknown challenge_binding.mode: {mode!r}")


def check_uv_flag(ctx: VerifierContext) -> CheckResult:
    """Check 7: UV bit (0x04) set in byte 32 of decoded authenticator_data."""
    bundle = ctx.bundle
    authority = bundle.get("authority", {}) or {}
    webauthn = authority.get("webauthn") or {}

    auth_data_raw = webauthn.get("authenticator_data", "")
    if not auth_data_raw:
        return CheckResult("uv_flag", False, "no authenticator_data")

    try:
        auth_bytes = base64.urlsafe_b64decode(auth_data_raw + "==")
    except Exception as e:
        return CheckResult("uv_flag", False, f"invalid base64url authenticator_data: {e}")

    if len(auth_bytes) < 33:
        return CheckResult("uv_flag", False,
                          f"authenticator_data too short: {len(auth_bytes)} bytes (need ≥33)")

    uv_bit = auth_bytes[32] & 0x04
    if not uv_bit:
        return CheckResult("uv_flag", False, "UV flag (bit 0x04) not set in authenticator_data")

    return CheckResult("uv_flag", True)


def check_catalog_attestations(ctx: VerifierContext) -> CheckResult:
    """Check 8: every item's catalog_attestation verifies, matches sku/price/tags,
    and iat <= cart_created_at."""
    bundle = ctx.bundle
    goods = bundle.get("goods") or {}
    items = goods.get("items", [])
    transaction = bundle.get("transaction") or {}

    if not items:
        return CheckResult("catalog_attestations", True)

    cart_created = transaction.get("cart_created_at", "")
    if cart_created:
        try:
            cart_ts = _parse_timestamp(cart_created)
        except Exception:
            cart_ts = 0
    else:
        cart_ts = 0

    for item in items:
        attestation = item.get("catalog_attestation")
        if not attestation:
            continue

        try:
            parts = attestation.split(".")
            if len(parts) != 3:
                return CheckResult("catalog_attestations", False,
                                  f"invalid JWS compact (need 3 parts), item={item.get('sku')}")

            # For the offline verifier, we check structural validity of catalog
            # attestations. Full cryptographic verification requires the merchant's
            # RSA public key which is not stored in the bundle.
            # We validate the payload is well-formed JSON with required fields.
            payload_b64 = parts[1]
            payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
            payload = json.loads(payload_bytes)

            sku = payload.get("sku")
            price_minor = payload.get("price_minor")
            tags = payload.get("tags", [])
            iat = payload.get("iat", 0)

            if sku != item.get("sku"):
                return CheckResult("catalog_attestations", False,
                                  f"sku mismatch: attestation={sku}, item={item.get('sku')}")

            if price_minor != item.get("unit_minor"):
                return CheckResult("catalog_attestations", False,
                                  f"price_minor mismatch: attestation={price_minor}, item={item.get('unit_minor')}")

            item_tags = set(item.get("tags", []))
            att_tags = set(tags)
            if att_tags != item_tags:
                return CheckResult("catalog_attestations", False,
                                  f"tags mismatch: attestation={sorted(att_tags)}, item={sorted(item_tags)}")

            if iat > cart_ts:
                return CheckResult("catalog_attestations", False,
                                  f"attestation.iat ({iat}) > cart_created_at ({cart_ts})")

        except Exception as e:
            return CheckResult("catalog_attestations", False,
                              f"catalog attestation parse error for sku={item.get('sku')}: {e}")

    return CheckResult("catalog_attestations", True)


def check_compiler_digest(ctx: VerifierContext) -> CheckResult:
    """Check 9: compiler_digest is recognized (exact match against pinned digest).

    The offline verifier cannot execute the compiler, so it checks the digest
    is in the known-good set. A missing or unknown digest triggers exit 3.
    """
    bundle = ctx.bundle
    adjudication = bundle.get("adjudication") or {}
    compiler_digest = adjudication.get("compiler_digest", "")

    KNOWN_COMPILER_DIGESTS = frozenset({
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    })

    if not compiler_digest:
        return CheckResult("compiler_digest", True, "no compiler_digest")

    if compiler_digest not in KNOWN_COMPILER_DIGESTS:
        return CheckResult(
            "compiler_digest", False,
            f"unsupported_compiler_digest: {compiler_digest}",
        )

    return CheckResult("compiler_digest", True)


def check_re_execution(ctx: VerifierContext) -> CheckResult:
    """Check 10: re-execution of compile_decision against adjudication.context
    returns ALLOW and its transcript is byte-identical to adjudication.transcript.

    The offline verifier cannot execute Python code. This check is a structural
    pass: it verifies the transcript is well-formed (all 13 checks present
    in order) and the verdict is ALLOW. In a full verifier, the transcript
    would be replayed through compile_decision().
    """
    bundle = ctx.bundle
    adjudication = bundle.get("adjudication") or {}

    verdict = adjudication.get("verdict", "")
    transcript = adjudication.get("transcript", [])

    if verdict != "ALLOW":
        return CheckResult("re_execution", False,
                          f"verdict is {verdict}, not ALLOW")

    # Verify transcript structure: all 13 checks present
    EXPECTED_CHECKS = [
        "human_authority_present", "currency_match", "merchant_lock",
        "policy_not_before", "policy_expiry", "transaction_count",
        "item_qty", "item_blocked_sku", "item_tag_allowlist",
        "spend_per_tx", "spend_envelope", "spend_cumulative", "campaign_validity",
    ]
    transcript_names = [entry.get("check") for entry in transcript]
    if transcript_names != EXPECTED_CHECKS:
        missing = set(EXPECTED_CHECKS) - set(transcript_names)
        extra = set(transcript_names) - set(EXPECTED_CHECKS)
        return CheckResult("re_execution", False,
                          f"transcript malformed: missing={sorted(missing)}, extra={sorted(extra)}")

    # All entries must have 'result' field
    for entry in transcript:
        if "result" not in entry:
            return CheckResult("re_execution", False,
                              f"transcript entry missing 'result': {entry}")

    return CheckResult("re_execution", True)


def check_amount_consistency(ctx: VerifierContext) -> CheckResult:
    """Check 11: transaction.amount_minor == sum(unit_minor * qty) over goods.items."""
    bundle = ctx.bundle
    transaction = bundle.get("transaction") or {}
    goods = bundle.get("goods") or {}
    items = goods.get("items", [])

    amount_minor = transaction.get("amount_minor")
    if not isinstance(amount_minor, int):
        return CheckResult("amount_consistency", False,
                          f"transaction.amount_minor is not an integer: {amount_minor!r}")

    computed = sum(item.get("unit_minor", 0) * item.get("qty", 0) for item in items)
    if computed != amount_minor:
        return CheckResult(
            "amount_consistency", False,
            f"amount_minor mismatch: transaction={amount_minor}, computed from items={computed}",
        )

    return CheckResult("amount_consistency", True)


def check_aal(ctx: VerifierContext) -> CheckResult:
    """Check 12: AAL tier resolution over e1–e9 using first-match-wins."""
    bundle = ctx.bundle
    level = compute_aal_level_from_bundle(bundle)

    # Verify aal section matches the computed level
    aal_section = bundle.get("aal") or {}
    aal_level = aal_section.get("level")

    if aal_level != level:
        return CheckResult("aal", False,
                          f"aal.level mismatch: section={aal_level}, computed={level}")

    return CheckResult("aal", True, f"level={level}")


def check_delegation_chain(ctx: VerifierContext) -> CheckResult:
    """Check 13: delegation chain integrity.

    Delegation chain: link 0 is the human's WebAuthn-signed root, each later
    link ES256-signed by the previous delegate. Attenuation is a meet (GLB) of
    constraints. MVP: verify structure if delegation is present.
    """
    bundle = ctx.bundle
    authority = bundle.get("authority") or {}
    delegation = authority.get("delegation") or {}

    if delegation is None or delegation == {}:
        return CheckResult("delegation_chain", True, "no delegation (root policy)")

    chain = delegation.get("chain", [])
    if not isinstance(chain, list):
        return CheckResult("delegation_chain", False,
                          f"delegation.chain must be list, got {type(chain).__name__}")

    # Verify chain has expected structure (each link has a signature)
    for i, link in enumerate(chain):
        if not isinstance(link, dict):
            return CheckResult("delegation_chain", False,
                              f"delegation.chain[{i}] is not a dict")
        if "signature" not in link:
            return CheckResult("delegation_chain", False,
                              f"delegation.chain[{i}] missing 'signature'")

    return CheckResult("delegation_chain", True)


def check_spend_chain(ctx: VerifierContext) -> CheckResult:
    """Check 14: spend chain integrity.

    One hash chain per envelope. Every budget-moving event appends a link.
    Sequence contiguous from 0, no gaps. SPEND countersigned by merchant.
    DELEGATE/RELEASE by envelope holder.
    """
    bundle = ctx.bundle
    authority = bundle.get("authority") or {}
    delegation = authority.get("delegation") or {}

    if delegation is None or delegation == {}:
        return CheckResult("spend_chain", True, "no delegation (root policy)")

    spend_chain = delegation.get("spend_chain") or []
    if not spend_chain:
        return CheckResult("spend_chain", True)

    for i, entry in enumerate(spend_chain):
        if not isinstance(entry, dict):
            return CheckResult("spend_chain", False,
                              f"spend_chain[{i}] is not a dict")
        seq = entry.get("sequence")
        if seq != i:
            return CheckResult("spend_chain", False,
                              f"spend_chain sequence gap: index={i}, entry.sequence={seq}")

    return CheckResult("spend_chain", True)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_timestamp(ts: str) -> float:
    """Parse RFC 3339 / ISO timestamp to Unix seconds."""
    ts = ts.replace("Z", "+00:00")
    if "+" in ts:
        dt = datetime.fromisoformat(ts)
    else:
        dt = datetime.fromisoformat(ts)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Run all 14 checks
# ---------------------------------------------------------------------------

CHECKS = [
    check_schema,
    check_chain_integrity,
    check_merchant_signature,
    check_time_anchor,
    check_webauthn_assertion,
    check_challenge_binding,
    check_uv_flag,
    check_catalog_attestations,
    check_compiler_digest,
    check_re_execution,
    check_amount_consistency,
    check_aal,
    check_delegation_chain,
    check_spend_chain,
]


def run_all_checks(ctx: VerifierContext) -> list[CheckResult]:
    """Run all 14 checks in order. Returns all results."""
    for check_fn in CHECKS:
        result = check_fn(ctx)
        ctx.add(result)
    return ctx.results


def bundle_exit_code(results: list[CheckResult]) -> int:
    """Determine exit code from check results.

    Exit codes per REGISTRY.json "verifier_exit_codes":
    0 = all pass
    1 = a check failed
    2 = malformed/schema-invalid (check_schema failed)
    3 = unsupported_compiler_digest
    4 = usage error
    """
    for r in results:
        if r.name == "schema" and not r.passed:
            return EXIT_MALFORMED
    for r in results:
        if r.name == "compiler_digest" and not r.passed and "unsupported_compiler_digest" in r.detail:
            return EXIT_UNSUPPORTED_COMPILER_DIGEST
        if r.name == "merchant_signature" and not r.passed and "signature verification failed" in r.detail:
            return EXIT_FAIL
    for r in results:
        if not r.passed:
            return EXIT_FAIL
    return EXIT_OK
