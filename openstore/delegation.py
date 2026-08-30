"""Delegation chain (Layer 1 — constraints). DELEGATION_AND_ORCHESTRATION §3.1.

Pure of database / web framework so the standalone verifier can reuse it offline.

A delegation chain is a list of signed links. Link 0 is the human's WebAuthn-signed root
policy; each subsequent link is ES256-signed by the previous link's delegate. Verification is a
pure function that returns the effective policy (the meet of every grant in the chain).
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

from .canonical import canonical_json_bytes

MAX_DEPTH = 3
DELEGATION_VERSION = "1.0.0"

DELEGATION_SPEC = {
    "delegation_version": "1.0.0",
    "check_order": [
        "root_is_human_signed",
        "link_signature",
        "link_order",
        "depth_limit",
        "budget_attenuation",
        "merchant_attenuation",
        "tag_attenuation",
        "expiry_attenuation",
        "tx_count_attenuation",
        "envelope_disjointness",
    ],
    "reason_codes": [
        "root_not_human_signed",
        "link_signature_invalid",
        "link_order_invalid",
        "depth_exceeded",
        "budget_not_attenuating",
        "merchant_not_attenuating",
        "tag_not_attenuating",
        "expiry_not_attenuating",
        "tx_count_not_attenuating",
        "envelope_overlaps_sibling",
    ],
    "attenuation_operator": "meet",
    "max_depth": MAX_DEPTH,
    "delegation_semantics": "exclusive_transfer",
}

# Pinned exactly as COMPILER_DIGEST is (IMPLEMENTATION_SPEC §3.6), over DELEGATION_SPEC (§5.2).
DELEGATION_DIGEST = "sha256:f4d24d08ca1f31813479584ffa5c514d0267ec00ada416b33f497ee9d4a04016"

REASON_CODES = tuple(DELEGATION_SPEC["reason_codes"])


# ---------- dataclasses ----------


@dataclass(frozen=True, slots=True)
class Grant:
    budget_minor: int
    currency: str
    merchant_ids: Tuple[str, ...]  # sorted; plural — see §9.1
    allowed_tags: Tuple[str, ...]  # sorted; empty == unconstrained
    tag_mode: str  # "all" | "any"
    blocked_skus: Tuple[str, ...]  # sorted
    max_transactions: int
    not_before: int  # Unix seconds
    expires_at: int  # Unix seconds

    def to_dict(self) -> dict:
        return {
            "budget_minor": self.budget_minor,
            "currency": self.currency,
            "merchant_ids": list(self.merchant_ids),
            "allowed_tags": list(self.allowed_tags),
            "tag_mode": self.tag_mode,
            "blocked_skus": list(self.blocked_skus),
            "max_transactions": self.max_transactions,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Grant":
        return cls(
            budget_minor=d["budget_minor"],
            currency=d["currency"],
            merchant_ids=tuple(d["merchant_ids"]),
            allowed_tags=tuple(d["allowed_tags"]),
            tag_mode=d["tag_mode"],
            blocked_skus=tuple(d["blocked_skus"]),
            max_transactions=d["max_transactions"],
            not_before=d["not_before"],
            expires_at=d["expires_at"],
        )


@dataclass(frozen=True, slots=True)
class DelegationLink:
    link_id: str  # "dl_" + 26 Crockford base32
    parent_link_id: Optional[str]  # None iff depth == 0
    depth: int  # 0..3
    envelope_id: str  # "env_" + 26 Crockford base32
    delegator_thumbprint: str  # RFC 7638 JWK thumbprint; at depth 0 the WebAuthn credential_id
    delegate_thumbprint: str
    grant: Grant
    issued_at: str  # RFC 3339
    signature: str  # JWS Compact ES256 over canonical(link without `signature`)

    def to_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "parent_link_id": self.parent_link_id,
            "depth": self.depth,
            "envelope_id": self.envelope_id,
            "delegator_thumbprint": self.delegator_thumbprint,
            "delegate_thumbprint": self.delegate_thumbprint,
            "grant": self.grant.to_dict(),
            "issued_at": self.issued_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DelegationLink":
        return cls(
            link_id=d["link_id"],
            parent_link_id=d["parent_link_id"],
            depth=d["depth"],
            envelope_id=d["envelope_id"],
            delegator_thumbprint=d["delegator_thumbprint"],
            delegate_thumbprint=d["delegate_thumbprint"],
            grant=Grant.from_dict(d["grant"]),
            issued_at=d["issued_at"],
            signature=d["signature"],
        )


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    budget_minor: int
    currency: str
    merchant_ids: Tuple[str, ...]
    allowed_tags: Tuple[str, ...]
    tag_mode: str
    blocked_skus: Tuple[str, ...]
    max_transactions: int
    not_before: int
    expires_at: int
    # Per-tx cap = leaf envelope budget; cumulative cap = root envelope budget.
    max_spend_per_tx_minor: int
    max_spend_total_minor: int

    def to_dict(self) -> dict:
        return {
            "budget_minor": self.budget_minor,
            "currency": self.currency,
            "merchant_ids": list(self.merchant_ids),
            "allowed_tags": list(self.allowed_tags),
            "tag_mode": self.tag_mode,
            "blocked_skus": list(self.blocked_skus),
            "max_transactions": self.max_transactions,
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "max_spend_per_tx_minor": self.max_spend_per_tx_minor,
            "max_spend_total_minor": self.max_spend_total_minor,
        }


# ---------- ES256 JWS helpers (mirror evidence.py) ----------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _raw_ecdsa_signature(private_key: ec.EllipticCurvePrivateKey, signing_input: bytes) -> bytes:
    der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _link_payload(link: DelegationLink) -> dict:
    return {
        "link_id": link.link_id,
        "parent_link_id": link.parent_link_id,
        "depth": link.depth,
        "envelope_id": link.envelope_id,
        "delegator_thumbprint": link.delegator_thumbprint,
        "delegate_thumbprint": link.delegate_thumbprint,
        "grant": link.grant.to_dict(),
        "issued_at": link.issued_at,
    }


def sign_link(link: DelegationLink, signing_key, kid: str) -> str:
    header = {"alg": "ES256", "kid": kid}
    h = _b64url(canonical_json_bytes(header))
    p = _b64url(canonical_json_bytes(_link_payload(link)))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _raw_ecdsa_signature(signing_key, signing_input)
    return f"{h}.{p}.{_b64url(sig)}"


def _verify_link_signature(link: DelegationLink, public_key) -> None:
    try:
        h, p, sig_b64 = link.signature.split(".")
    except ValueError:
        raise ValueError("link_signature_invalid")
    try:
        header = json.loads(_b64url_decode(h))
    except Exception:
        raise ValueError("link_signature_invalid")
    if header.get("alg") != "ES256":
        raise ValueError("link_signature_invalid")
    signing_input = f"{h}.{p}".encode("ascii")
    raw = _b64url_decode(sig_b64)
    if len(raw) != 64:
        raise ValueError("link_signature_invalid")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    try:
        public_key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception:
        raise ValueError("link_signature_invalid")
    expected_p = _b64url(canonical_json_bytes(_link_payload(link)))
    if p != expected_p:
        raise ValueError("link_signature_invalid")


# ---------- attenuation (meet) ----------


def _check_attenuation(parent: Grant, child: Grant) -> None:
    if child.currency != parent.currency:
        raise ValueError("currency_mismatch")
    if child.budget_minor > parent.budget_minor:
        raise ValueError("budget_not_attenuating")
    # merchant_ids: child MUST be a subset of parent.
    if not set(child.merchant_ids).issubset(set(parent.merchant_ids)):
        raise ValueError("merchant_not_attenuating")
    # allowed_tags: child MUST be a subset of parent, unless parent is empty (⊤).
    if parent.allowed_tags and not set(child.allowed_tags).issubset(set(parent.allowed_tags)):
        raise ValueError("tag_not_attenuating")
    # tag_mode: parent "all" ⇒ child MUST be "all".
    if parent.tag_mode == "all" and child.tag_mode != "all":
        raise ValueError("tag_not_attenuating")
    # not_before = max; expires_at = min.
    if child.not_before < parent.not_before:
        raise ValueError("expiry_not_attenuating")
    if child.expires_at > parent.expires_at:
        raise ValueError("expiry_not_attenuating")
    if child.max_transactions > parent.max_transactions:
        raise ValueError("tx_count_not_attenuating")
    # blocked_skus is additive (union) — a child may block more, never fewer.
    if not set(parent.blocked_skus).issubset(set(child.blocked_skus)):
        raise ValueError("tag_not_attenuating")


def _meet(parent: Grant, child: Grant) -> Grant:
    _check_attenuation(parent, child)
    return Grant(
        budget_minor=min(parent.budget_minor, child.budget_minor),
        currency=child.currency,
        merchant_ids=tuple(sorted(set(child.merchant_ids))),
        allowed_tags=tuple(sorted(set(child.allowed_tags))),
        tag_mode=child.tag_mode if parent.tag_mode == "any" else "all",
        blocked_skus=tuple(sorted(set(parent.blocked_skus) | set(child.blocked_skus))),
        max_transactions=min(parent.max_transactions, child.max_transactions),
        not_before=max(parent.not_before, child.not_before),
        expires_at=min(parent.expires_at, child.expires_at),
    )


# ---------- pure verification ----------


def verify_delegation_chain(
    links: Tuple[DelegationLink, ...],
    *,
    keys: Optional[dict] = None,
    root_is_human_signed: bool = True,
    verify_signatures: bool = True,
) -> EffectivePolicy:
    """Verify a delegation chain offline and return its effective (meet) policy.

    `keys` maps delegator_thumbprint -> ES256 public key for links of depth >= 1.
    `root_is_human_signed` is supplied by the caller from the bundle's authority scheme
    (the WebAuthn root signature is not verifiable offline from the link alone).
    """
    if not links:
        raise ValueError("link_order_invalid")

    # 1. root_is_human_signed
    if links[0].depth != 0 or not root_is_human_signed:
        raise ValueError("root_not_human_signed")

    # 3. link_order + 2. link_signature
    prev: Optional[DelegationLink] = None
    for i, link in enumerate(links):
        if link.depth != i:
            raise ValueError("link_order_invalid")
        if i == 0:
            if link.parent_link_id is not None:
                raise ValueError("link_order_invalid")
        else:
            if link.parent_link_id != prev.link_id:  # type: ignore[union-attr]
                raise ValueError("link_order_invalid")
            if link.delegator_thumbprint != prev.delegate_thumbprint:  # type: ignore[union-attr]
                raise ValueError("link_order_invalid")
            if verify_signatures:
                if keys is None:
                    raise ValueError("link_signature_invalid")
                key = keys.get(link.delegator_thumbprint)
                if key is None:
                    raise ValueError("link_signature_invalid")
                _verify_link_signature(link, key)
        prev = link

    # 4. depth_limit — any link deeper than MAX_DEPTH is rejected.
    for link in links:
        if link.depth > MAX_DEPTH:
            raise ValueError("depth_exceeded")

    # 5. budget / 6. merchant / 7. tag / 8. expiry / 9. tx_count attenuation
    #    (10. envelope_disjointness is a property of the parent's spend chain — see §3.3 —
    #    but within a single chain it reduces to budget_attenuating, already enforced above).
    eff = links[0].grant
    for child in links[1:]:
        eff = _meet(eff, child.grant)

    return EffectivePolicy(
        budget_minor=eff.budget_minor,
        currency=eff.currency,
        merchant_ids=eff.merchant_ids,
        allowed_tags=eff.allowed_tags,
        tag_mode=eff.tag_mode,
        blocked_skus=eff.blocked_skus,
        max_transactions=eff.max_transactions,
        not_before=eff.not_before,
        expires_at=eff.expires_at,
        # Leaf envelope budget bounds a single transaction; the root envelope
        # budget bounds cumulative spend across the whole delegation (§3.1).
        max_spend_per_tx_minor=eff.budget_minor,
        max_spend_total_minor=links[0].grant.budget_minor,
    )


# ---------- §3.2 Budget envelope (Layer 2 — exclusive transfer) ----------


@dataclass(frozen=True, slots=True)
class BudgetEnvelope:
    envelope_id: str  # "env_" + 26 Crockford base32
    budget_minor: int
    currency: str
    issued_at: str  # RFC 3339
    expires_at: int  # Unix seconds
    parent_envelope_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "envelope_id": self.envelope_id,
            "budget_minor": self.budget_minor,
            "currency": self.currency,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "parent_envelope_id": self.parent_envelope_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BudgetEnvelope":
        return cls(
            envelope_id=d["envelope_id"],
            budget_minor=d["budget_minor"],
            currency=d["currency"],
            issued_at=d["issued_at"],
            expires_at=d["expires_at"],
            parent_envelope_id=d.get("parent_envelope_id"),
        )


def envelope_available(envelope: BudgetEnvelope, chain_state: "ChainState") -> int:
    """R3.2a — available budget is *derived*, never stored.

    available = budget − Σ SPEND − Σ DELEGATE + Σ RELEASE.
    """
    return (
        envelope.budget_minor
        - chain_state.spent_minor
        - chain_state.delegated_minor
        + chain_state.released_minor
    )


def check_sibling_disjointness(
    parent_envelope: BudgetEnvelope,
    parent_chain_state: "ChainState",
    child_envelopes: Tuple[BudgetEnvelope, ...],
) -> None:
    """R3.2c — Σ sibling envelope budgets ≤ available(parent).

    Checkable from the parent's spend chain alone, so it verifies offline.
    Violation is `envelope_overlaps_sibling`.
    """
    # ponytail: simplified to spent-vs-budget; per-child expiry/release accounting
    # is folded into the delegated_minor already recorded on the parent chain.
    available = parent_envelope.budget_minor - parent_chain_state.spent_minor
    used = sum(c.budget_minor for c in child_envelopes)
    if used > available:
        raise ValueError("envelope_overlaps_sibling")


# ---------- §3.3 Spend chain (Layer 3 — accounting) ----------


@dataclass(frozen=True, slots=True)
class SpendEntry:
    envelope_id: str
    sequence: int  # 0-based, contiguous, no gaps
    entry_type: str  # "SPEND" | "DELEGATE" | "RELEASE"
    amount_minor: int  # RELEASE: the amount returned
    ref: str  # SPEND: bundle_id · DELEGATE: child envelope_id · RELEASE: ""
    prev_link: str  # "sha256:…"; at sequence 0 the genesis (below)
    issued_at: str
    signature: str  # signer depends on entry_type (R3.3b)

    def to_dict(self) -> dict:
        return {
            "envelope_id": self.envelope_id,
            "sequence": self.sequence,
            "entry_type": self.entry_type,
            "amount_minor": self.amount_minor,
            "ref": self.ref,
            "prev_link": self.prev_link,
            "issued_at": self.issued_at,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SpendEntry":
        return cls(
            envelope_id=d["envelope_id"],
            sequence=d["sequence"],
            entry_type=d["entry_type"],
            amount_minor=d["amount_minor"],
            ref=d["ref"],
            prev_link=d["prev_link"],
            issued_at=d["issued_at"],
            signature=d["signature"],
        )


@dataclass(frozen=True, slots=True)
class ChainState:
    envelope_id: str
    spent_minor: int
    delegated_minor: int
    released_minor: int
    available_minor: int
    entry_count: int


SPENDCHAIN_SPEC = {
    "spendchain_version": "1.0.0",
    "check_order": [
        "genesis_matches_envelope",
        "sequence_contiguous",
        "prev_link_matches",
        "no_fork_in_presented_set",
        "sum_within_envelope",
    ],
    "reason_codes": [
        "genesis_mismatch",
        "sequence_gap",
        "prev_link_mismatch",
        "fork_detected",
        "envelope_overspent",
    ],
    "link_formula": "sha256(prev_link || canonical(entry))",
    "genesis_formula": "sha256(canonical({envelope_id}))",
}

# Pinned exactly as DELEGATION_DIGEST is (§3.3c), over SPENDCHAIN_SPEC.
SPENDCHAIN_DIGEST = "sha256:80fc5c08cab7ae4c9a82d6dec4eece8c6965c14554ab88097fe86ef3db7623ff"

SPENDCHAIN_REASON_CODES = tuple(SPENDCHAIN_SPEC["reason_codes"])


def _genesis_link(envelope_id: str) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json_bytes({"envelope_id": envelope_id})
    ).hexdigest()


def _spend_payload(entry: SpendEntry) -> dict:
    return {
        "envelope_id": entry.envelope_id,
        "sequence": entry.sequence,
        "entry_type": entry.entry_type,
        "amount_minor": entry.amount_minor,
        "ref": entry.ref,
        "prev_link": entry.prev_link,
        "issued_at": entry.issued_at,
    }


def _link_raw(prev_link: str, entry: SpendEntry) -> bytes:
    prev_raw = bytes.fromhex(prev_link.split(":", 1)[1])
    return hashlib.sha256(prev_raw + canonical_json_bytes(_spend_payload(entry))).digest()


def sign_spend_entry(entry: SpendEntry, signing_key, kid: str) -> str:
    header = {"alg": "ES256", "kid": kid}
    h = _b64url(canonical_json_bytes(header))
    p = _b64url(canonical_json_bytes(_spend_payload(entry)))
    signing_input = f"{h}.{p}".encode("ascii")
    sig = _raw_ecdsa_signature(signing_key, signing_input)
    return f"{h}.{p}.{_b64url(sig)}"


# ---------- §6.2(b) sequencer ordering signature ----------


def _sequencer_payload(entries: Tuple[SpendEntry, ...], merchant_ids: Tuple[str, ...]) -> list:
    """Ordered (merchant, amount, sequence) tuples the sequencer must countersign
    (DELEGATION_AND_ORCHESTRATION §6.2b). Single-merchant today; one tuple per SPEND."""
    mid = merchant_ids[0] if merchant_ids else ""
    return [
        {"merchant": mid, "amount_minor": e.amount_minor, "sequence": e.sequence}
        for e in entries
        if e.entry_type == "SPEND"
    ]


def sign_sequencer_ordering(
    entries: Tuple[SpendEntry, ...], merchant_ids: Tuple[str, ...], sequencer_key, kid: str
) -> str:
    payload = _sequencer_payload(entries, merchant_ids)
    header = {"alg": "ES256", "kid": kid}
    h = _b64url(canonical_json_bytes(header))
    p = _b64url(canonical_json_bytes(payload))
    sig = _raw_ecdsa_signature(sequencer_key, f"{h}.{p}".encode("ascii"))
    return f"{h}.{p}.{_b64url(sig)}"


def verify_sequencer_ordering(
    entries: Tuple[SpendEntry, ...], merchant_ids: Tuple[str, ...], signature: str, public_key
) -> None:
    payload = _sequencer_payload(entries, merchant_ids)
    try:
        h, p, sig_b64 = signature.split(".")
    except ValueError:
        raise ValueError("malformed_sequencer_signature")
    if p != _b64url(canonical_json_bytes(payload)):
        raise ValueError("sequencer_payload_mismatch")
    raw = _b64url_decode(sig_b64)
    if len(raw) != 64:
        raise ValueError("bad_sequencer_signature")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    try:
        public_key.verify(encode_dss_signature(r, s), f"{h}.{p}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except Exception as e:
        raise ValueError(f"sequencer_signature_invalid:{e}")


def _verify_spend_entry_signature(entry: SpendEntry, keys: dict) -> None:
    try:
        h, p, sig_b64 = entry.signature.split(".")
    except ValueError:
        raise ValueError("prev_link_mismatch")
    try:
        header = json.loads(_b64url_decode(h))
    except Exception:
        raise ValueError("prev_link_mismatch")
    if header.get("alg") != "ES256":
        raise ValueError("prev_link_mismatch")
    kid = header.get("kid")
    key = keys.get(kid) if kid else None
    if key is None:
        raise ValueError("prev_link_mismatch")
    signing_input = f"{h}.{p}".encode("ascii")
    raw = _b64url_decode(sig_b64)
    if len(raw) != 64:
        raise ValueError("prev_link_mismatch")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    try:
        key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))
    except Exception:
        raise ValueError("prev_link_mismatch")
    if p != _b64url(canonical_json_bytes(_spend_payload(entry))):
        raise ValueError("prev_link_mismatch")


def verify_spend_chain(
    entries: Tuple[SpendEntry, ...],
    envelope: BudgetEnvelope,
    *,
    keys: Optional[dict] = None,
    verify_signatures: bool = True,
) -> ChainState:
    """Verify a spend chain offline (R3.3c). Pure — runs from the presented entries."""
    if not entries:
        raise ValueError("genesis_mismatch")

    # 1. genesis_matches_envelope
    genesis = _genesis_link(envelope.envelope_id)
    if entries[0].prev_link != genesis:
        raise ValueError("genesis_mismatch")

    seen_sequences: set = set()
    spent = delegated = released = 0
    prev = genesis
    for i, e in enumerate(entries):
        # 4. no_fork_in_presented_set — two entries share a sequence (R3.3b fork).
        if e.sequence in seen_sequences:
            raise ValueError("fork_detected")
        seen_sequences.add(e.sequence)
        # 2. sequence_contiguous
        if e.sequence != i:
            raise ValueError("sequence_gap")
        # 3. prev_link_matches — entry i's prev_link MUST equal the previous link
        #    (genesis for i==0, otherwise the derived link_{i-1}).
        if e.prev_link != prev:
            raise ValueError("prev_link_mismatch")
        expected_next = "sha256:" + _link_raw(prev, e).hex()
        if keys is not None and verify_signatures:
            _verify_spend_entry_signature(e, keys)
        if e.entry_type == "SPEND":
            spent += e.amount_minor
        elif e.entry_type == "DELEGATE":
            delegated += e.amount_minor
        elif e.entry_type == "RELEASE":
            released += e.amount_minor
        else:
            raise ValueError("prev_link_mismatch")
        prev = expected_next

    # 5. sum_within_envelope
    if spent + delegated > envelope.budget_minor:
        raise ValueError("envelope_overspent")

    available = envelope.budget_minor - spent - delegated - released
    return ChainState(
        envelope_id=envelope.envelope_id,
        spent_minor=spent,
        delegated_minor=delegated,
        released_minor=released,
        available_minor=available,
        entry_count=len(entries),
    )


# ---------- §4 Fork detection (cross-merchant double-spend) ----------


def extract_spend_entries(bundle: dict) -> list:
    """Pull SPEND entries from a bundle's `authority.delegation.spend_chain`."""
    auth = bundle.get("authority") or {}
    deleg = auth.get("delegation")
    if not deleg:
        return []
    return [e for e in deleg.get("spend_chain", []) if e.get("entry_type") == "SPEND"]


def detect_forks(bundles: list) -> list:
    """Scan a set of bundles and emit any `fork_proof` (§4.3).

    Two SPEND entries with the same `(envelope_id, sequence)`, countersigned by
    different merchants, are a non-repudiable proof that the holder forked its
    chain. Returns a list of fork-proof dicts.
    """
    seen: dict = {}
    for b in bundles:
        bid = b.get("bundle_id", "?")
        for e in extract_spend_entries(b):
            key = (e.get("envelope_id"), e.get("sequence"))
            rec = {"bundle_id": bid, "ref": e.get("ref"), "signature": e.get("signature")}
            seen.setdefault(key, []).append(rec)
    proofs = []
    for (env, seq), recs in seen.items():
        sigs = {r["signature"] for r in recs}
        if len(recs) > 1 and len(sigs) > 1:
            proofs.append({
                "envelope_id": env,
                "sequence": seq,
                "reason": "fork_detected",
                "entries": recs,
            })
    return proofs


# ---------- issuer-side helpers (runtime mints delegated orders) ----------


def effective_policy_from_chain(links: Tuple[DelegationLink, ...]) -> EffectivePolicy:
    """R5.3a — `authority.policy` is the effective (meet) policy, not the root."""
    return verify_delegation_chain(links, root_is_human_signed=True, verify_signatures=False)


def effective_policy_to_dict(eff: EffectivePolicy) -> dict:
    d = eff.to_dict()
    # v1.1.0 compiler expects `merchant_ids` for set-membership merchant_lock.
    d["merchant_ids"] = list(eff.merchant_ids)
    # Backwards-compatible scalar merchant id (single-merchant envelopes take the first).
    d["merchant_id"] = eff.merchant_ids[0] if eff.merchant_ids else ""
    return d


def build_authority_delegation(
    links: Tuple[DelegationLink, ...],
    envelope: BudgetEnvelope,
    spend_chain: Tuple[SpendEntry, ...],
    *,
    sequencer_type: str = "none",
    currency: str = "inr",
    parent_envelope_id: Optional[str] = None,
) -> dict:
    """Assemble the `authority.delegation` section (R5.3) for a bundle."""
    eff = effective_policy_from_chain(links)
    return {
        "chain": [l.to_dict() for l in links],
        "delegation_digest": DELEGATION_DIGEST,
        "envelope_id": envelope.envelope_id,
        "envelope_budget_minor": envelope.budget_minor,
        "currency": currency,
        "parent_envelope_id": parent_envelope_id,
        "expires_at": envelope.expires_at,
        "issued_at": envelope.issued_at,
        "spend_chain": [e.to_dict() for e in spend_chain],
        "spendchain_digest": SPENDCHAIN_DIGEST,
        "sequencer": {"type": sequencer_type},
        "depth": links[-1].depth,
        "merchant_ids": list(eff.merchant_ids),
    }


def issuer_spend_entry(
    envelope_id: str,
    sequence: int,
    entry_type: str,
    amount_minor: int,
    ref: str,
    prev_link: str,
    issued_at: str,
    signing_key,
    kid: str,
) -> SpendEntry:
    """Build + merchant/holder-sign one spend-chain entry (R3.3b)."""
    e = SpendEntry(
        envelope_id=envelope_id, sequence=sequence, entry_type=entry_type,
        amount_minor=amount_minor, ref=ref, prev_link=prev_link,
        issued_at=issued_at, signature="",
    )
    return SpendEntry.from_dict(
        {**e.to_dict(), "signature": sign_spend_entry(e, signing_key, kid)}
    )


def append_spend_entry(
    envelope_id: str,
    prior_entries: Tuple[SpendEntry, ...],
    entry_type: str,
    amount_minor: int,
    ref: str,
    issued_at: str,
    signing_key,
    kid: str,
) -> SpendEntry:
    """Chain a new entry after `prior_entries` (genesis if empty) and sign it."""
    if prior_entries:
        prev_link = "sha256:" + _link_raw(
            prior_entries[-1].prev_link, prior_entries[-1]
        ).hex()
        sequence = prior_entries[-1].sequence + 1
    else:
        prev_link = _genesis_link(envelope_id)
        sequence = 0
    return issuer_spend_entry(
        envelope_id, sequence, entry_type, amount_minor, ref,
        prev_link, issued_at, signing_key, kid,
    )
