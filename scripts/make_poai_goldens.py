#!/usr/bin/env python3
"""Generate GOLDEN/{canonical,hashchain,poai}/*.json vectors (Stage 4, S4.3).

Every canonical JSON vector is produced by running canonical_json_bytes() over the
real code (R0.7: never hand-written from memory). Every PoAI bundle is assembled
by create_poai_bundle() using a fixed EC P-256 keypair. Tampered variants are
constructed by surgical JSON mutation so only the targeted field is changed.

Fixed timestamps: 2030-01-01T00:00:00Z for issued_at / evaluated_at so bundles
are deterministic across runs. A fixed EC P-256 keypair (deterministic seed) is
generated once and stored under GOLDEN/poai/keys/.

Run: uv run python scripts/make_poai_goldens.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

ROOT = Path(__file__).resolve().parent.parent
CANONICAL_DIR = ROOT / "GOLDEN" / "canonical"
HASHCHAIN_DIR = ROOT / "GOLDEN" / "hashchain"
POAI_DIR = ROOT / "GOLDEN" / "poai"
POAI_KEYS = POAI_DIR / "keys"
POAI_JWKS = POAI_DIR / "jwks"

FIXED_ISSUE_TIME = "2030-01-01T00:00:00Z"
FIXED_EVAL_TIME = "2030-01-01T00:00:01Z"

MERCHANT_ID = "gelateria-roma"
KEY_VERSION = "1"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_raw(b64: str) -> bytes:
    return base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))


def load_or_create_poai_key() -> tuple[bytes, dict]:
    POAI_KEYS.mkdir(parents=True, exist_ok=True)
    der_path = POAI_KEYS / "ec_p256_poai.der"
    if der_path.exists():
        priv_bytes = der_path.read_bytes()
    else:
        priv_key = ec.generate_private_key(ec.SECP256R1())
        priv_bytes = priv_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        der_path.write_bytes(priv_bytes)
        pem_path = POAI_KEYS / "ec_p256_poai.pem"
        pem_path.write_bytes(
            priv_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    priv_key = serialization.load_der_private_key(priv_bytes, password=None)
    pub_key = priv_key.public_key()
    pub_nums = pub_key.public_numbers()
    x_b64 = b64u(pub_nums.x.to_bytes(32, "big"))
    y_b64 = b64u(pub_nums.y.to_bytes(32, "big"))

    kid = f"{MERCHANT_ID}-key-{KEY_VERSION}"
    jwks = {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "x": x_b64,
                "y": y_b64,
                "alg": "ES256",
                "kid": kid,
            }
        ]
    }
    return priv_bytes, jwks


def _es256_sign(priv_bytes: bytes, signing_input: str) -> str:
    priv_key = serialization.load_der_private_key(priv_bytes, password=None)
    der_sig = priv_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return b64u(raw_sig)


def _sign_bundle_jws(bundle_id: str, issued_at: str, root: str,
                      priv_bytes: bytes, kid: str) -> str:
    header = json.dumps({"alg": "ES256", "kid": kid, "typ": "JWT"}, sort_keys=True, separators=(",", ":"))
    payload = json.dumps({"bundle_id": bundle_id, "issued_at": issued_at, "root": root},
                          sort_keys=True, separators=(",", ":"))
    header_b64 = b64u(header.encode("utf-8"))
    payload_b64 = b64u(payload.encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}"
    sig_b64 = _es256_sign(priv_bytes, signing_input)
    return f"{signing_input}.{sig_b64}"


def _make_time_anchor(root: str, salt_b64: str) -> dict:
    salt_digest_input = json.dumps({"root": root, "salt": salt_b64}, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(salt_digest_input).hexdigest()
    return {"type": "merkle_daily", "salt": salt_b64, "root": root, "digest": f"sha256:{digest}"}


def _canonical_json_bytes(obj):
    if obj is None:
        return b"null"
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _section_hash(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _build_chain(sections_data: dict) -> tuple[list[str], str]:
    """Build hash chain and return (links, root)."""
    from openstore.core.poai import SECTION_ORDER
    links_out: list[str] = []
    prev_digest = b""
    for name in SECTION_ORDER:
        c_i = sections_data.get(name, b"null")
        if name not in sections_data:
            c_i = b"null"
        if not links_out:
            prev_digest = hashlib.sha256(c_i).digest()
        else:
            prev_digest = hashlib.sha256(prev_digest + c_i).digest()
        links_out.append(f"sha256:{prev_digest.hex()}")
    return links_out, prev_digest.hex()


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, sort_keys=True, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# S4.1: Canonical JSON vectors
# ---------------------------------------------------------------------------
def make_canonical_vectors() -> None:
    """Pin at least 5 canonical JSON vectors covering nested objects, unicode,
    empty containers, key-order independence, and integer fidelity."""

    import sys
    sys.path.insert(0, str(ROOT / "src"))
    from openstore.core.poai import canonical_json_bytes

    cases = [
        {
            "name": "simple_object",
            "input": {"b": 2, "a": 1},
            "note": "sorted keys regardless of input order",
        },
        {
            "name": "nested_object",
            "input": {"outer": {"z": 1, "a": 2}, "x": 0},
            "note": "nested keys also sorted",
        },
        {
            "name": "unicode_value",
            "input": {"name": "\u0930\u093e\u091c", "qty": 5},
            "note": "hindi characters, integers unquoted",
        },
        {
            "name": "empty_list",
            "input": {"items": []},
            "note": "empty containers preserved as []",
        },
        {
            "name": "empty_object",
            "input": {"metadata": {}},
            "note": "empty objects preserved as {}",
        },
        {
            "name": "null_section",
            "input": None,
            "note": "Python None → b'null'",
        },
        {
            "name": "integer_no_quotes",
            "input": {"amount": 21000, "qty": 1},
            "note": "integers are unquoted (not string '21000')",
        },
        {
            "name": "mixed_types",
            "input": {
                "sku": "GEL-VAN-500",
                "price_minor": 21000,
                "tags": ["vegan", "dairy-free"],
                "catalog_digest": "sha256:abc123",
                "merchant_id": "gelateria-roma",
                "iat": 1787000000,
            },
            "note": "real catalog attestation payload",
        },
    ]

    for case in cases:
        data = canonical_json_bytes(case["input"])
        out = {
            "name": case["name"],
            "note": case["note"],
            "input_repr": repr(case["input"]),
            "canonical_hex": data.hex(),
            "canonical_b64u": b64u(data),
            "as_text": data.decode("utf-8"),
        }
        _write_json(CANONICAL_DIR / f"{case['name']}.json", out)

    print(f"wrote {len(cases)} canonical vectors to {CANONICAL_DIR}")


# ---------------------------------------------------------------------------
# S4.1 / §3.3.11: Hash chain vectors
# ---------------------------------------------------------------------------
def make_hashchain_vectors() -> None:
    """Pin hash chain vectors covering: null sections, all sections populated,
    and a chain-break case."""
    from openstore.core.poai import SECTION_ORDER

    # Case 1: all null sections (all b"null")
    null_data = {name: b"null" for name in SECTION_ORDER}
    links_null, root_null = _build_chain(null_data)
    _write_json(HASHCHAIN_DIR / "all_null_sections.json", {
        "description": "all 9 sections are JSON null",
        "sections": {name: None for name in SECTION_ORDER},
        "links": links_null,
        "root": root_null,
    })

    # Case 2: one section non-null (transaction only)
    tx = {"merchant_id": "m_test", "amount_minor": 10000}
    sec_data = {name: b"null" for name in SECTION_ORDER}
    sec_data["transaction"] = _canonical_json_bytes(tx)
    links_one, root_one = _build_chain(sec_data)
    _write_json(HASHCHAIN_DIR / "one_section.json", {
        "description": "only transaction section populated",
        "sections": {name: None for name in SECTION_ORDER},
        "sections_override": {"transaction": tx},
        "links": links_one,
        "root": root_one,
    })

    # Case 3: all sections populated
    full_sections = {
        "transaction": {"merchant_id": "m_test", "amount_minor": 10000},
        "human_intent": {"request_digest": "sha256:test"},
        "authority": {"scheme": "webauthn"},
        "goods": {"cart_hash": "sha256:cart"},
        "agent": {"client_id": "agent-1", "scopes": ["checkout:confirm"], "token_jti": "jti-1"},
        "adjudication": {"compiler_digest": "sha256:compiler"},
        "notification": {"receipt_digest": "sha256:receipt"},
        "aal": {"level": 2},
        "campaign": None,
    }
    full_sec_data = {}
    for name in SECTION_ORDER:
        full_sec_data[name] = _canonical_json_bytes(full_sections.get(name))
    links_full, root_full = _build_chain(full_sec_data)
    _write_json(HASHCHAIN_DIR / "all_sections.json", {
        "description": "all 9 sections populated (campaign null)",
        "sections": full_sections,
        "links": links_full,
        "root": root_full,
    })

    print(f"wrote 3 hash chain vectors to {HASHCHAIN_DIR}")


# ---------------------------------------------------------------------------
# S4.3: PoAI bundle generation
# ---------------------------------------------------------------------------
def make_bundle_base(priv_bytes: bytes, jwks: dict) -> dict:
    """Build the base sections for AAL2 bundle (policy-mode WebAuthn, no campaign)."""
    kid = jwks["keys"][0]["kid"]

    # auth_data: rpIdHash(32 bytes) + flags(1) + signCount(4) — total 37 bytes
    # flags byte: 0x05 = UP (0x01) | UV (0x04)
    auth_data = bytearray(37)
    auth_data[32] = 0x05  # UP | UV flags
    auth_data[33:37] = (4).to_bytes(4, "big")  # sign_count = 4

    transaction = {
        "merchant_id": MERCHANT_ID,
        "checkout_id": "chk_test_001",
        "cart_created_at": "2030-01-01T00:00:00Z",
        "amount_minor": 21000,
        "currency": "INR",
        "psp": {"provider": "razorpay", "order_id": "order_test", "payment_link_id": "link_test"},
    }

    human_intent = {
        "request_digest": hashlib.sha256("Buy gelato".encode("utf-8")).hexdigest(),
        "request_text": "Buy gelato",
        "captured_at": "2030-01-01T00:00:00Z",
        "channel": "mcp",
        "channel_message_id": "msg_test",
        "agent_plan": {
            "model": "gpt-4o",
            "interpretation": "user wants to order gelato",
            "constraints_extracted": ["vegan", "≤₹500"],
            "candidates_considered": 3,
            "plan_digest": hashlib.sha256("plan_v1".encode()).hexdigest(),
        },
    }

    authority = {
        "scheme": "webauthn",
        "policy": "pol_test_001",
        "policy_hash": hashlib.sha256("policy_content".encode()).hexdigest(),
        "webauthn": {
            "credential_id": "cred_es256_test",
            "client_data_json": b64u(b'{"type":"webauthn.get","challenge":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","origin":"https://openstore.test","crossOrigin":false}'),
            "authenticator_data": b64u(bytes(auth_data)),
            "signature": b64u(b"\x00" * 72),
            "uv": True,
            "sign_count": 4,
            "signed_at": "2030-01-01T00:00:00Z",
            "assertion_max_age_seconds": 86400,
            "challenge_binding": {"mode": "policy"},
        },
        "enrolment": {
            "public_key": {"kty": "EC", "crv": "P-256", "x": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "y": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
            "aaguid": "00000000-0000-0000-0000-000000000000",
            "attestation_format": "none",
            "enrolled_at": "2030-01-01T00:00:00Z",
        },
        "presentation": None,
        "delegation": None,
    }

    goods = {
        "cart_hash": hashlib.sha256('[{"sku":"GEL-VAN-500","qty":1,"unit_minor":21000,"tags":["vegan","dairy-free"]}]'.encode()).hexdigest(),
        "cart_version": 1,
        "items": [
            {
                "sku": "GEL-VAN-500",
                "qty": 1,
                "unit_minor": 21000,
                "tags": ["dairy-free", "vegan"],
                "catalog_attestation": None,
            }
        ],
        "catalog_attestations_valid": True,
    }

    agent = {
        "client_id": "buyer-agent-001",
        "display_name": "OpenStore Buyer Agent",
        "token_jti": "jti-agent-001",
        "scopes": ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
        "consent_granted_at": "2030-01-01T00:00:00Z",
        "edge_identity": None,
    }

    adjudication = {
        "compiler_version": "1.0.0",
        "compiler_digest": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "policy_schema_version": 2,
        "evaluated_at": FIXED_EVAL_TIME,
        "context": {
            "cart_items": goods["items"],
            "merchant_id": MERCHANT_ID,
            "currency": "INR",
        },
        "verdict": "ALLOW",
        "reason_code": None,
        "transcript": [
            {"check": "human_authority_present", "result": "pass", "reason_code": None},
            {"check": "currency_match", "result": "pass", "reason_code": None},
            {"check": "merchant_lock", "result": "pass", "reason_code": None},
            {"check": "policy_not_before", "result": "pass", "reason_code": None},
            {"check": "policy_expiry", "result": "pass", "reason_code": None},
            {"check": "transaction_count", "result": "pass", "reason_code": None},
            {"check": "item_qty", "result": "pass", "reason_code": None},
            {"check": "item_blocked_sku", "result": "pass", "reason_code": None},
            {"check": "item_tag_allowlist", "result": "pass", "reason_code": None},
            {"check": "spend_per_tx", "result": "pass", "reason_code": None},
            {"check": "spend_envelope", "result": "pass", "reason_code": None},
            {"check": "spend_cumulative", "result": "pass", "reason_code": None},
            {"check": "campaign_validity", "result": "pass", "reason_code": None},
        ],
    }

    notification = {
        "sent_at": "2030-01-01T00:00:02Z",
        "channel": "discord",
        "receipt_digest": hashlib.sha256("receipt_content".encode()).hexdigest(),
    }

    return {
        "kid": kid,
        "priv_bytes": priv_bytes,
        "transaction": transaction,
        "human_intent": human_intent,
        "authority": authority,
        "goods": goods,
        "agent": agent,
        "adjudication": adjudication,
        "notification": notification,
    }


def _assemble_bundle(base: dict, level: int, predicates: dict, reasons: list[str],
                     campaign: dict | None = None, bundle_id: str = "poai_test_bundle") -> dict:
    """Assemble a full PoAI bundle."""
    from openstore.core.poai import SECTION_ORDER

    priv_bytes = base["priv_bytes"]
    kid = base["kid"]

    sections = {
        "transaction": base["transaction"],
        "human_intent": base["human_intent"],
        "authority": base["authority"],
        "goods": base["goods"],
        "agent": base["agent"],
        "adjudication": base["adjudication"],
        "notification": base["notification"],
        "aal": {"level": level, "predicates": predicates, "reasons": reasons},
        "campaign": campaign,
    }

    sections_data = {}
    for name in SECTION_ORDER:
        val = sections.get(name)
        sections_data[name] = _canonical_json_bytes(val)

    links, root = _build_chain(sections_data)
    merchant_sig = _sign_bundle_jws(bundle_id, FIXED_ISSUE_TIME, root, priv_bytes, kid)

    salt_b64 = b64u(secrets.token_bytes(16))
    time_anchor = _make_time_anchor(root, salt_b64)

    return {
        "poai_version": "0.1",
        "bundle_id": bundle_id,
        "issued_at": FIXED_ISSUE_TIME,
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
            "links": links,
            "root": root,
            "merchant_signature": merchant_sig,
            "time_anchor": time_anchor,
        },
    }


def make_poai_vectors() -> None:
    """Generate PoAI bundle vectors (known-good, tampered variants)."""
    priv_bytes, jwks = load_or_create_poai_key()

    POAI_JWKS.mkdir(parents=True, exist_ok=True)
    _write_json(POAI_JWKS / f"{MERCHANT_ID}.json", jwks)

    base = make_bundle_base(priv_bytes, jwks)

    # --- AAL2 known-good bundle ---
    aal2_predicates = {
        "e1": True,
        "e2": True,
        "e3": True,
        "e4": True,
        "e5": False,
        "e6": False,
        "e7": True,
        "e8": True,
        "e9": True,
    }
    aal2_reasons = [
        "Proposed liability position (not a network rule): ...the human authorized a standing policy with a fresh, user-verified signature, and this cart compiled clean against it; this is evidence of authorized intent, with final allocation resting with the network and issuer."
    ]

    bundle_aal2 = _assemble_bundle(
        base, level=2, predicates=aal2_predicates, reasons=aal2_reasons,
        bundle_id="poai_test_bundle_aal2"
    )
    _write_json(POAI_DIR / "bundle_aal2.json", bundle_aal2)

    # --- AAL3 bundle: cart-bound challenge (e5 = True) ---
    aal3_predicates = {
        "e1": True, "e2": True, "e3": True, "e4": True,
        "e5": True,  # cart-bound challenge
        "e6": False, "e7": True, "e8": True, "e9": True,
    }
    # Modify the authority to have cart-bound challenge
    aal3_authority = dict(base["authority"])
    aal3_authority["webauthn"] = dict(base["authority"]["webauthn"])
    aal3_authority["webauthn"]["challenge_binding"] = {
        "mode": "cart",
        "cart_hash": base["goods"]["cart_hash"],
    }
    # Modify goods to have cart_attestations_valid
    aal3_goods = dict(base["goods"])
    aal3_goods["catalog_attestations_valid"] = True

    aal3_base = dict(base)
    aal3_base["authority"] = aal3_authority
    aal3_base["goods"] = aal3_goods

    aal3_reasons = [
        "Proposed liability position (not a network rule): ...the human's authenticator signed this exact cart with user verification; this is the strongest merchant-side evidence of authorized intent available."
    ]

    bundle_aal3 = _assemble_bundle(
        aal3_base, level=3, predicates=aal3_predicates, reasons=aal3_reasons,
        bundle_id="poai_test_bundle_aal3"
    )
    _write_json(POAI_DIR / "bundle_aal3.json", bundle_aal3)

    # --- Tampered: amount_minor changed ---
    tampered_amount = dict(bundle_aal2)
    tampered_amount["transaction"] = dict(tampered_amount["transaction"])
    tampered_amount["transaction"]["amount_minor"] = 99999
    _write_json(POAI_DIR / "bundle_tampered_amount.json", tampered_amount)

    # --- Tampered: transcript modified ---
    tampered_transcript = dict(bundle_aal2)
    tampered_transcript["adjudication"] = dict(tampered_transcript["adjudication"])
    tampered_transcript["adjudication"]["transcript"] = [
        {"check": "human_authority_present", "result": "pass", "reason_code": None},
        {"check": "currency_match", "result": "fail", "reason_code": "policy.currency_mismatch"},
    ]
    _write_json(POAI_DIR / "bundle_tampered_transcript.json", tampered_transcript)

    # --- Bad merchant signature ---
    bad_sig_bundle = dict(bundle_aal2)
    bad_sig_bundle["chain"] = dict(bad_sig_bundle["chain"])
    bad_sig_bundle["chain"]["merchant_signature"] = "eyJhbGciOiJFUzI1NiIsImtpZCI6ImdlbGF0ZXJpYS1yb21hLWtleS0xIiwidHlwIjoiSldUIn0.eyJidW5kbGVfaWQiOiJwb2FpX3Rlc3RfYnVuZGxlX2FhbDIiLCJpc3N1ZWRfYXQiOiIyMDMwLTAxLTAxVDAwOjAwOjAwWiIsInJvb3QiOiI2ZmQ0YmE5YjVjYTk4MjhhM2Y0NjdlMjMzMmMxMjNhYTgyODM2ZGMxOGQxOTRmNjM2NGUwYjgyMzhhZjhlZmQxMyJ9.INVALID"
    _write_json(POAI_DIR / "bundle_bad_merchant_sig.json", bad_sig_bundle)

    # --- Missing time anchor ---
    missing_anchor = dict(bundle_aal2)
    missing_anchor["chain"] = dict(missing_anchor["chain"])
    missing_anchor["chain"]["time_anchor"] = None
    _write_json(POAI_DIR / "bundle_missing_anchor.json", missing_anchor)

    print(f"wrote PoAI bundle vectors to {POAI_DIR}")
    print(f"wrote JWKS to {POAI_JWKS}")


def main() -> None:
    make_canonical_vectors()
    make_hashchain_vectors()
    make_poai_vectors()
    print("done")


if __name__ == "__main__":
    main()
