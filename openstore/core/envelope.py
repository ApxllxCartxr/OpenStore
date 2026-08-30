import base64
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .cbor import canonical_cbor, sha256
from .did import did_from_pubkey, pubkey_from_did
from .signing import sign_digest, verify_jws


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s) -> bytes:
    if isinstance(s, bytes):
        s = s.decode("ascii")
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)

ENVELOPE_VERSION = 1
DEFAULT_TTL = 3600

_SIGNED_KEYS = (
    "version",
    "issuer_did",
    "issued_at",
    "expires_at",
    "payload_type",
    "payload_digest",
    "payload",
    "inherent_claims",
    "parent_digest",
    "agent",
    "delegation",
)


@dataclass
class AgentTrustEnvelope:
    issuer_did: str
    issued_at: int
    expires_at: int
    payload_type: str
    payload: bytes
    payload_digest: bytes = b""
    inherent_claims: Optional[dict] = None
    parent_digest: Optional[bytes] = None
    agent: Optional[dict] = None
    delegation: Optional[dict] = None
    poai_bundle: Optional[dict] = None
    proof: Optional[dict] = None

    def __post_init__(self):
        if not self.payload_digest:
            self.payload_digest = sha256(self.payload)
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")

    def _signed_fields(self) -> dict:
        f = {
            "version": ENVELOPE_VERSION,
            "issuer_did": self.issuer_did,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "payload_type": self.payload_type,
            "payload_digest": self.payload_digest,
            "payload": self.payload,
        }
        for key in ("inherent_claims", "parent_digest", "agent", "delegation", "poai_bundle"):
            val = getattr(self, key)
            if val is not None:
                f[key] = val
        return f

    def digest(self) -> bytes:
        return sha256(canonical_cbor(self._signed_fields()))

    def sign(self, private_key):
        self.proof = {
            "alg": "EdDSA",
            "kid": self.issuer_did,
            "jws": sign_digest(private_key, self.digest(), kid=self.issuer_did),
        }
        return self

    def verify(self) -> None:
        if self.proof is None:
            raise ValueError("missing proof")
        claimed = verify_jws(self.proof["jws"], pubkey_from_did(self.issuer_did))
        if claimed != self.digest():
            raise ValueError("envelope digest mismatch")
        if int(time.time()) > self.expires_at:
            raise ValueError("envelope expired")
        if self.delegation is not None:
            md = self.delegation.get("max_depth")
            depth = self.delegation.get("depth", 0)
            if md is not None and depth > md:
                raise ValueError("delegation depth exceeded")

    def to_dict(self) -> dict:
        out = self._signed_fields()
        out["payload"] = _b64(out["payload"])
        out["payload_digest"] = _b64(out["payload_digest"])
        if out.get("parent_digest") is not None:
            out["parent_digest"] = _b64(out["parent_digest"])
        out["proof"] = self.proof
        return out

    def serialize(self) -> bytes:
        return canonical_cbor(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            issuer_did=d["issuer_did"],
            issued_at=d["issued_at"],
            expires_at=d["expires_at"],
            payload_type=d["payload_type"],
            payload=_b64d(d["payload"]),
            payload_digest=_b64d(d.get("payload_digest", "")),
            inherent_claims=d.get("inherent_claims"),
            parent_digest=_b64d(d["parent_digest"]) if d.get("parent_digest") else None,
            agent=d.get("agent"),
            delegation=d.get("delegation"),
            poai_bundle=d.get("poai_bundle"),
            proof=d.get("proof"),
        )
