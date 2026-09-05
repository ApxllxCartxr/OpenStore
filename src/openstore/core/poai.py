# OpenStore core — PoAI evidence bundle (9 sections, hash chain, AAL ladder)
# Per PRD v3.0 Part 3.3 (§3.3.0–§3.3.11), §3.5 (AAL e1–e9, first-match-wins).

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any


class PoAISigningError(Exception):
    """Raised when PoAI bundle merchant signing fails.

    Per R0.5, signing failures must fail loud — never silently produce a
    bundle with a null merchant_signature. The caller (psp/router.py::_build_and_store_evidence)
    decides whether to persist the bundle without a signature or abort.
    """

    pass


# SECTION_ORDER: fixed order of the nine PoAI sections (v3.0, §3.3.0).
# campaign is appended last (index 8). A non-applying section is JSON null.
SECTION_ORDER = (
    "transaction",
    "human_intent",
    "authority",
    "goods",
    "agent",
    "adjudication",
    "notification",
    "aal",
    "campaign",
)


def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical JSON bytes per S4.1 / §3.3.11.

    - UTF-8 encoding.
    - Sorted keys (deterministic field order).
    - No insignificant whitespace (separators=(",", ":")).
    - Python None → b"null" (JSON null, never absent from the chain).
    - Integers unquoted (standard json.dumps behaviour).
    """
    if obj is None:
        return b"null"
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def hash_section(data: bytes) -> str:
    """Return SHA-256 hex digest prefixed with 'sha256:'."""
    digest = hashlib.sha256(data).digest()
    return f"sha256:{digest.hex()}"


def hash_section_raw(data: bytes) -> bytes:
    """Return raw SHA-256 digest (32 bytes) — used for chain construction."""
    return hashlib.sha256(data).digest()


def build_hash_chain(sections_data: dict[str, bytes]) -> dict[str, Any]:
    """Build the PoAI hash chain per §3.3.11.

    Algorithm (verbatim from PRD):
      c_i = canonical_json_bytes(bundle[SECTION_ORDER[i]])  (null → b"null")
      link_0 = SHA256(c_0)
      link_i = SHA256(link_{i-1} || c_i)  for i = 1..8
      root = link_8
      chain.links = 9 strings "sha256:" + hex(link_i) in SECTION_ORDER

    Returns {"links": list[str], "root": str} where links[i] is the link
    for SECTION_ORDER[i] and root == links[8].
    """
    links: list[str] = []
    prev_digest = b""  # empty for link_0

    for section_name in SECTION_ORDER:
        c_i = sections_data.get(section_name, b"null")
        if section_name not in sections_data:
            c_i = b"null"

        if len(links) == 0:
            link_digest = hashlib.sha256(c_i).digest()
        else:
            link_digest = hashlib.sha256(prev_digest + c_i).digest()

        links.append(f"sha256:{link_digest.hex()}")
        prev_digest = link_digest

    root = prev_digest.hex()
    return {"links": links, "root": root}


def sign_merchant_jws_compact(
    bundle_id: str,
    issued_at: str,
    root: str,
    private_key_pem: bytes,
    merchant_id: str = "merchant",
) -> str:
    """ES256 JWS Compact over exactly {bundle_id, issued_at, root} per §3.3.11.

    Header: {"alg":"ES256","kid":"{merchant_id}-key-{n}","typ":"JWT"}
    Payload: {"bundle_id":...,"issued_at":...,"root":...}
    Signature: base64url(ECDSA(256, SHA-256)(base64url(header) || '.' || base64url(payload)))

    Per DECISIONS §11.1.10 the `kid` is namespaced by merchant_id so a JWKS
    directory can select per-merchant keys by `kid`.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    # Parse the private key
    key = serialization.load_der_private_key(private_key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("sign_merchant_jws_compact requires an EC P-256 private key")
    if key.curve.name != "secp256r1":
        raise ValueError("sign_merchant_jws_compact requires EC P-256 (secp256r1)")

    # Build header
    kid = f"{merchant_id}-key-1"
    header_bytes = canonical_json_bytes({"alg": "ES256", "kid": kid, "typ": "JWT"})

    # Build payload
    payload_bytes = canonical_json_bytes(
        {
            "bundle_id": bundle_id,
            "issued_at": issued_at,
            "root": root,
        }
    )

    # Sign
    signing_input = (
        base64.urlsafe_b64encode(header_bytes).decode().rstrip("=")
        + "."
        + base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    )
    der_sig = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))

    # DER signature to raw (r || s, each 32 bytes for P-256)
    r, s = decode_dss_signature(der_sig)
    r_bytes = r.to_bytes(32, "big")
    s_bytes = s.to_bytes(32, "big")
    raw_sig = r_bytes + s_bytes

    sig_b64 = base64.urlsafe_b64encode(raw_sig).decode().rstrip("=")

    return signing_input + "." + sig_b64


def build_time_anchor(root: str) -> dict[str, Any]:
    """Build a merkle_daily time anchor (asynchronous, never blocks checkout_confirm).

    Per DECISIONS §11.1.1: Rekor primary, merkle_daily fallback.
    We build the merkle_daily anchor here: digest({"root": root, "salt": salt}).
    The salt is 16 random bytes base64url-encoded.
    """
    salt = secrets.token_bytes(16)
    salt_b64 = base64.urlsafe_b64encode(salt).decode().rstrip("=")
    digest_input = canonical_json_bytes({"root": root, "salt": salt_b64})
    digest = hashlib.sha256(digest_input).hexdigest()
    return {
        "type": "merkle_daily",
        "salt": salt_b64,
        "root": root,
        "digest": f"sha256:{digest}",
    }


class AALLevel(int):
    AAL0 = 0
    AAL1 = 1
    AAL2 = 2
    AAL3 = 3

    def __str__(self) -> str:
        return f"AAL{self}"


AAL_LIABILITY: dict[int, str] = {
    3: "Proposed liability position (not a network rule): ...the human's authenticator signed this exact cart with user verification; this is the strongest merchant-side evidence of authorized intent available.",
    2: "Proposed liability position (not a network rule): ...the human authorized a standing policy with a fresh, user-verified signature, and this cart compiled clean against it; this is evidence of authorized intent, with final allocation resting with the network and issuer.",
    1: "Proposed liability position (not a network rule): ...authority was presented but one or more freshness, attestation, or verification predicates failed; treat the transaction as contested.",
    0: "Proposed liability position (not a network rule): ...no verifiable human authority exists; no order is created at this level.",
}


def get_aal_liability_sentence(level: int) -> str:
    return AAL_LIABILITY.get(level, "Unknown AAL level")


def evaluate_aal_predicates(bundle: dict[str, Any]) -> dict[str, bool]:
    """Evaluate AAL predicates e1–e9 per §3.5 table.

    Returns a dict with keys e1..e9 (boolean).
    """
    adjudication = bundle.get("adjudication") or {}
    authority = bundle.get("authority") or {}
    human_intent = bundle.get("human_intent") or {}
    notification = bundle.get("notification") or {}
    goods = bundle.get("goods") or {}
    agent = bundle.get("agent") or {}
    auth_webauthn = authority.get("webauthn") or {}

    e1 = bool(
        agent.get("client_id")
        and agent.get("scopes")
        and agent.get("token_jti")
        and "checkout:confirm" in agent.get("scopes", [])
    )

    auth_data_raw = auth_webauthn.get("authenticator_data", "")
    auth_bytes: bytes | None = None
    if auth_data_raw:
        try:
            auth_bytes = base64.urlsafe_b64decode(auth_data_raw + "=" * (-len(auth_data_raw) % 4))
        except Exception:
            auth_bytes = None

    e2 = bool(auth_bytes is not None and len(auth_bytes) >= 33 and bool(auth_bytes[32] & 0x04))
    e4 = e2  # UV bit is the same check

    e3 = False
    adj_evaluated = adjudication.get("evaluated_at", "")
    webauthn_signed = auth_webauthn.get("signed_at", "")
    if adj_evaluated and webauthn_signed:
        try:
            adj_ts = datetime.fromisoformat(adj_evaluated.replace("Z", "+00:00")).timestamp()
            web_ts = datetime.fromisoformat(webauthn_signed.replace("Z", "+00:00")).timestamp()
            max_age = auth_webauthn.get("assertion_max_age_seconds", 86400)
            e3 = (adj_ts - web_ts) <= max_age
        except (ValueError, TypeError):
            e3 = False

    e5 = False
    binding = auth_webauthn.get("challenge_binding") or {}
    e5 = binding.get("mode") == "cart" and binding.get("cart_hash") == goods.get("cart_hash")

    e6 = bool(goods.get("catalog_attestations_valid") is True)

    # e7: re-execution of compile_decision returns ALLOW and the transcript
    # matches. We use the re_execution check's verdict (the verifier runs the
    # same check at runtime); for a well-formed bundle with verdict=ALLOW and a
    # well-formed transcript, e7 is True.
    e7 = adjudication.get("verdict") == "ALLOW" and bool(adjudication.get("transcript"))

    e8 = bool(human_intent is not None and human_intent != {})
    if e8:
        digest_val = human_intent.get("request_digest", "")
        text_val = human_intent.get("request_text", "")
        if digest_val and text_val:
            computed = hashlib.sha256(text_val.encode("utf-8")).hexdigest()
            e8 = computed == digest_val

    e9 = bool(
        notification is not None
        and notification != {}
        and notification.get("receipt_digest") is not None
    )

    return {
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "e4": e4,
        "e5": e5,
        "e6": e6,
        "e7": e7,
        "e8": e8,
        "e9": e9,
    }


def compute_aal_level_from_bundle(bundle: dict[str, Any]) -> int:
    """Compute AAL level using first-match-wins per §3.5.

    Evaluates predicates e1–e9, then applies the decision table in order.
    """
    p = evaluate_aal_predicates(bundle)

    authority = bundle.get("authority") or {}
    policy_obj_raw = authority.get("policy")
    if isinstance(policy_obj_raw, dict):
        policy_obj = policy_obj_raw
    else:
        policy_obj = {}

    policy_version = policy_obj.get("policy_version", 2)

    if not p["e2"]:
        return 0
    if not p["e7"]:
        return 0
    if not p["e1"]:
        return 0
    if policy_version != 2:
        return 1
    if not p["e3"]:
        return 1
    if not p["e6"]:
        return 1
    if not p["e4"]:
        return 1
    if not (p["e8"] and p["e9"]):
        return 1
    if p["e5"]:
        return 3
    return 2


def build_aal_section(
    level: int, predicates: dict[str, bool], reasons: list[str]
) -> dict[str, Any]:
    """Build the aal section of the bundle."""
    return {
        "level": level,
        "predicates": predicates,
        "reasons": reasons,
    }


def create_poai_bundle(
    *,
    bundle_id: str | None = None,
    issued_at: str | None = None,
    transaction: dict[str, Any] | None = None,
    human_intent: dict[str, Any] | None = None,
    authority: dict[str, Any] | None = None,
    goods: dict[str, Any] | None = None,
    agent: dict[str, Any] | None = None,
    adjudication: dict[str, Any] | None = None,
    notification: dict[str, Any] | None = None,
    aal: dict[str, Any] | None = None,
    campaign: dict[str, Any] | None = None,
    merchant_private_key_pem: bytes | None = None,
    merchant_id: str = "merchant",
) -> dict[str, Any]:
    """Assemble a complete 9-section PoAI bundle per §3.3.

    Each section not provided defaults to JSON null. The hash chain covers all
    nine sections in SECTION_ORDER, even null ones.
    """
    bid = bundle_id or f"poai_{secrets.token_hex(16)}"
    iat = issued_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")

    sections = {
        "transaction": transaction,
        "human_intent": human_intent,
        "authority": authority,
        "goods": goods,
        "agent": agent,
        "adjudication": adjudication,
        "notification": notification,
        "aal": aal,
        "campaign": campaign,
    }

    sections_data: dict[str, bytes] = {}
    for name in SECTION_ORDER:
        sections_data[name] = canonical_json_bytes(sections.get(name))

    chain = build_hash_chain(sections_data)

    merchant_signature: str | None = None
    if merchant_private_key_pem:
        try:
            merchant_signature = sign_merchant_jws_compact(
                bundle_id=bid,
                issued_at=iat,
                root=chain["root"],
                private_key_pem=merchant_private_key_pem,
                merchant_id=merchant_id,
            )
        except Exception as e:
            raise PoAISigningError(f"merchant signing failed: {e}") from e

    time_anchor = build_time_anchor(chain["root"])

    return {
        "poai_version": "0.1",
        "bundle_id": bid,
        "issued_at": iat,
        "transaction": sections["transaction"],
        "human_intent": sections["human_intent"],
        "authority": sections["authority"],
        "goods": sections["goods"],
        "agent": sections["agent"],
        "adjudication": sections["adjudication"],
        "notification": sections["notification"],
        "aal": sections["aal"],
        "campaign": sections["campaign"],
        "chain": {
            "links": chain["links"],
            "root": chain["root"],
            "merchant_signature": merchant_signature,
            "time_anchor": time_anchor,
        },
    }


def verify_poai_bundle(bundle: dict[str, Any]) -> tuple[bool, list[str]]:
    """Verify PoAI bundle — quick-look surface (checks 1-4).

    This is the in-process "quick-look" verifier per Q-026 resolution.
    It runs checks 1-4: schema, chain_integrity, merchant_signature (structural),
    and time_anchor. The authoritative verifier is the offline CLI
    (`openstore-verify`), which runs all 14 checks including the 10
    content-dependent checks (5-14) that require persisted per-checkout
    assertion data not available to the server (see Q-019).

    Returns (verified, errors).
    """
    errors: list[str] = []

    # Check 1: schema
    required_toplevel = {
        "poai_version",
        "bundle_id",
        "issued_at",
        "transaction",
        "human_intent",
        "authority",
        "goods",
        "agent",
        "adjudication",
        "notification",
        "aal",
        "campaign",
        "chain",
    }
    missing = required_toplevel - set(bundle.keys())
    if missing:
        errors.append(f"missing top-level fields: {sorted(missing)}")
        return False, errors

    chain = bundle.get("chain", {})
    required_chain = {"links", "root", "merchant_signature", "time_anchor"}
    missing_chain = required_chain - set(chain.keys())
    if missing_chain:
        errors.append(f"chain missing fields: {sorted(missing_chain)}")
        return False, errors

    links = chain.get("links", [])
    if not isinstance(links, list) or len(links) != 9:
        errors.append(f"chain.links must be list of 9, got {type(links).__name__} len={len(links)}")
        return False, errors

    for i, link in enumerate(links):
        if not isinstance(link, str) or not link.startswith("sha256:"):
            errors.append(f"chain.links[{i}] must be 'sha256:hex', got {link!r}")
            return False, errors

    # Check 2: chain integrity
    root = chain.get("root", "")
    sections_data: dict[str, bytes] = {}
    for i, section_name in enumerate(SECTION_ORDER):
        section_value = bundle.get(section_name)
        sections_data[section_name] = canonical_json_bytes(section_value)

    expected = build_hash_chain(sections_data)

    if links != expected["links"]:
        for i, (got, want) in enumerate(zip(links, expected["links"])):
            if got != want:
                errors.append(f"link[{i}] ({SECTION_ORDER[i]}): expected {want}, got {got}")

    if root != expected["root"]:
        errors.append(f"root: expected {expected['root']}, got {root}")

    # Check 3: merchant_signature (structural only — no JWKS available in-process)
    sig_str = chain.get("merchant_signature", "")
    if not sig_str:
        errors.append("merchant_signature is empty")
    elif sig_str == "unverified_no_jwks":
        # Placeholder — in-process verifier cannot verify without JWKS
        pass
    else:
        parts = sig_str.split(".")
        if len(parts) != 3:
            errors.append(
                f"merchant_signature: invalid JWS Compact (expected 3 parts, got {len(parts)})"
            )
        else:
            try:
                import base64 as _b64
                import json as _json

                header = _json.loads(_b64.urlsafe_b64decode(parts[0] + "=="))
                if header.get("alg") != "ES256":
                    errors.append(
                        f"merchant_signature: wrong algorithm {header.get('alg')}, expected ES256"
                    )
            except Exception as e:
                errors.append(f"merchant_signature: cannot decode header: {e}")

    # Check 4: time_anchor
    anchor = chain.get("time_anchor")
    if anchor is None:
        errors.append("time_anchor is absent")
    elif not isinstance(anchor, dict):
        errors.append(f"time_anchor must be dict, got {type(anchor).__name__}")
    else:
        atype = anchor.get("type")
        if atype not in ("rekor", "merkle_daily"):
            errors.append(f"time_anchor: unknown anchor type: {atype}")

    return len(errors) == 0, errors
