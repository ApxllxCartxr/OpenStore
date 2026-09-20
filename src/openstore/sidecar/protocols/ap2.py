"""AP2 — **a security layer over UCP, not a fourth envelope.**

Its own specification says so: *"AP2 operates as a security feature within a
Commerce Protocol"* and *"AP2 is designed explicitly to be compatible with the
Universal Commerce Protocol (UCP)"*. Catalogue APIs and checkout updates are
outside its scope. Building it as a peer of MCP/UCP/ACP would be a
nicer-looking lie, so it rides on `ucp.py` and the toggle reads `UCP+AP2`.

Two mandates, not the launch-era three:

- **Checkout Mandate** — the Shopping Agent is authorized to purchase *this*
  checkout. Carries a `checkout_hash` binding it to the merchant-signed Checkout
  JWT, and a `vct` schema version.
- **Payment Mandate** — authorization to pay. Carries `checkout_hash`,
  `transaction_id`, and in autonomous mode a `cnf` claim holding the agent's
  public key.

The merchant's obligations, in order: sign a Checkout JWT, receive the Checkout
Mandate, validate it, **confirm the hash of our Checkout JWT matches the
mandate's `checkout_hash`**, check any open mandate's constraints, and return a
Checkout Receipt JWT.

The two modes, in the spec's own words:

- *"Human Present (Direct): The User directly sees the closed Checkout and
  approves it and its payment explicitly."* — supported. `/agentic/approve` is
  the Trusted Surface the spec describes.
- *"Human Not Present (Autonomous)"* — **refused.** That mode is the `mandate`
  Authority kind, defined and refused in v1 (ADR-0017).

ES256 verification is reused from `admission/signatures.py`. There is not a
second JWT verifier in this codebase and there must not be.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec

from openstore.sidecar.admission.signatures import public_jwk
from openstore.sidecar.core.codes import Protocol, ReasonCode
from openstore.sidecar.evidence.keys import KeyRecord, sign, verify_signature

CHECKOUT_MANDATE_VCT = "mandate.checkout.open.1"
PAYMENT_MANDATE_VCT = "mandate.payment.1"
CHECKOUT_RECEIPT_VCT = "receipt.checkout.1"


@unique
class Mode(StrEnum):
    """The spec's own names, not paraphrases."""

    HUMAN_PRESENT = "human-present-direct"
    AUTONOMOUS = "human-not-present-autonomous"


class Ap2Error(Exception):
    def __init__(self, code: ReasonCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        return {
            "protocol": Protocol.AP2.value,
            "error": {"code": self.code.value, "detail": self.detail},
        }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_jwt(claims: dict[str, Any], record: KeyRecord) -> str:
    """ES256, compact serialization. Reuses the evidence keyring's signer."""
    header = {"alg": "ES256", "typ": "JWT", "kid": record.kid}
    signing_input = f"{_b64url(json.dumps(header, sort_keys=True, separators=(',', ':')).encode())}.{
        _b64url(json.dumps(claims, sort_keys=True, separators=(',', ':')).encode())}"
    signature = sign(record, signing_input.encode())
    return f"{signing_input}.{_b64url(base64.b64decode(signature))}"


def decode_jwt(token: str, jwk: dict[str, Any]) -> dict[str, Any]:
    """Verify, then read. A JWT read before it is verified is attacker input
    that has already been trusted."""
    try:
        header_b64, claims_b64, signature_b64 = token.split(".")
    except ValueError:
        raise Ap2Error(ReasonCode.SIGNATURE_INVALID, "not a compact JWT") from None

    header = json.loads(_unb64url(header_b64))
    if header.get("alg") != "ES256":
        # `alg: none` and algorithm confusion, refused at the door.
        raise Ap2Error(
            ReasonCode.SIGNATURE_INVALID, f"mandates are ES256, not {header.get('alg')!r}"
        )

    signature = base64.b64encode(_unb64url(signature_b64)).decode()
    if not verify_signature(jwk, f"{header_b64}.{claims_b64}".encode(), signature):
        raise Ap2Error(ReasonCode.SIGNATURE_INVALID, "mandate signature does not verify")

    claims: dict[str, Any] = json.loads(_unb64url(claims_b64))
    return claims


def checkout_hash(checkout_jwt: str) -> str:
    """The hash the Checkout Mandate binds to.

    Taken over the **compact JWT as sent**, not over re-encoded claims: the
    agent hashed the string it received, and re-encoding would produce a
    different one for the same document.
    """
    return _b64url(hashlib.sha256(checkout_jwt.encode()).digest())


@dataclass(frozen=True)
class CheckoutMandate:
    raw: str
    claims: dict[str, Any]

    @property
    def vct(self) -> str:
        return str(self.claims.get("vct", ""))

    @property
    def mode(self) -> Mode:
        """Autonomous mode is identified by the `cnf` claim holding the agent's
        own key — that is the spec's marker for the agent acting alone."""
        return Mode.AUTONOMOUS if "cnf" in self.claims else Mode.HUMAN_PRESENT


def sign_checkout(claims: dict[str, Any], record: KeyRecord) -> str:
    """The merchant-signed Checkout JWT the mandate binds to."""
    return encode_jwt({**claims, "vct": "checkout.1"}, record)


def verify_checkout_mandate(
    mandate_jwt: str,
    *,
    agent_jwk: dict[str, Any],
    our_checkout_jwt: str,
) -> CheckoutMandate:
    """The merchant's obligations, in the spec's order.

    The `checkout_hash` comparison is the load-bearing one: it is what stops an
    agent presenting a mandate the user signed for a different basket. Without
    it the mandate proves only that *some* checkout was approved.
    """
    claims = decode_jwt(mandate_jwt, agent_jwk)
    mandate = CheckoutMandate(raw=mandate_jwt, claims=claims)

    if mandate.vct != CHECKOUT_MANDATE_VCT:
        raise Ap2Error(
            ReasonCode.METHOD_NOT_SUPPORTED,
            f"this sidecar reads {CHECKOUT_MANDATE_VCT}; the mandate declares "
            f"{mandate.vct or 'no vct'}",
        )

    expected = checkout_hash(our_checkout_jwt)
    if claims.get("checkout_hash") != expected:
        raise Ap2Error(
            ReasonCode.AUTHORITY_STALE,
            "the mandate's checkout_hash does not match this merchant's Checkout. It "
            "authorizes a different basket.",
        )

    if mandate.mode is Mode.AUTONOMOUS:
        # Human Not Present is the `mandate` Authority kind: defined,
        # registered, and refused in v1.
        raise Ap2Error(
            ReasonCode.AUTHORITY_KIND_NOT_ENABLED,
            "Human Not Present (Autonomous) is the `mandate` Authority kind, which this "
            "Merchant does not accept. Human Present (Direct) completes through the "
            "approve ceremony.",
        )

    return mandate


def checkout_receipt(
    mandate: CheckoutMandate, record: KeyRecord, *, transaction_id: str, approve_url: str
) -> str:
    """The Checkout Receipt JWT the merchant returns."""
    return encode_jwt(
        {
            "vct": CHECKOUT_RECEIPT_VCT,
            "checkout_hash": mandate.claims["checkout_hash"],
            "transaction_id": transaction_id,
            "completion": "buyer_escalation",
            "approve_url": approve_url,
        },
        record,
    )


def agent_jwk_from_key(private_key: ec.EllipticCurvePrivateKey, kid: str) -> dict[str, Any]:
    """Test and demo helper: the agent's published key, in JWK form."""
    return dict(public_jwk(private_key, kid))
