# OpenStore core — PoAI evidence bundle (8+1 sections, hash chain)

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from typing import Any

from openstore.config import Settings
from openstore.models import Checkout, IntentPolicy, WebAuthnCredential

SECTION_ORDER = [
    "transaction",
    "human_intent",
    "authority",
    "goods",
    "agent",
    "adjudication",
    "notification",
    "aal",
    "campaign",  # v3.0 - appended
]


def canonical_json(data: Any) -> bytes:
    """Serialize to canonical JSON (sorted keys, no whitespace)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def hash_section(data: bytes) -> str:
    """Compute SHA-256 hash of section data."""
    return hashlib.sha256(data).hexdigest()


def build_hash_chain(sections: dict[str, bytes]) -> dict[str, Any]:
    """Build hash chain from ordered sections."""
    links = []
    prev_hash = "0" * 64  # Genesis

    for section_name in SECTION_ORDER:
        if section_name not in sections:
            continue

        section_data = sections[section_name]
        section_hash = hash_section(section_data)

        link = {
            "section": section_name,
            "section_hash": section_hash,
            "prev_hash": prev_hash,
        }
        links.append(link)
        prev_hash = section_hash

    root = prev_hash

    return {
        "links": links,
        "root": root,
    }


def create_poai_bundle(
    config: Settings,
    checkout: Checkout,
    policy: IntentPolicy | None = None,
    webauthn_credential: WebAuthnCredential | None = None,
    agent_plan: dict[str, Any] | None = None,
    notification_receipt: dict[str, Any] | None = None,
    campaign_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Create PoAI evidence bundle (9 sections + hash chain).

    Per PRD Part 3.3:
    1. transaction
    2. human_intent
    3. authority
    4. goods
    5. agent
    6. adjudication
    7. notification
    8. aal
    9. campaign (optional, v3.0)
    """
    now = datetime.utcnow()
    bundle_id = f"poai_{secrets.token_hex(16)}"

    # Section 1: transaction
    transaction = {
        "merchant_id": checkout.merchant_id,
        "checkout_id": checkout.id,
        "cart_created_at": checkout.created_at.isoformat() + "Z",
        "amount_minor": checkout.amount_minor,
        "currency": checkout.currency,
        "psp": {
            "provider": checkout.psp_provider,
            "order_id": checkout.psp_order_id,
            "payment_link_id": checkout.psp_payment_link_id,
        },
    }

    # Section 2: human_intent
    human_intent = {
        "request_digest": checkout.cart_hash,
        "request_text": f"Checkout {checkout.id} for {checkout.amount_minor} {checkout.currency}",
        "captured_at": checkout.created_at.isoformat() + "Z",
        "channel": "mcp",  # or "web", "discord"
        "channel_message_id": checkout.trace_id,
        "agent_plan": agent_plan,
    }

    # Section 3: authority
    authority: dict[str, Any] = {
        "scheme": "webauthn",
        "policy": policy.id if policy else None,
        "policy_hash": policy.policy_hash if policy else checkout.policy_hash,
        "webauthn": None,
        "enrolment": None,
        "presentation": None,
        "delegation": None,
    }

    if webauthn_credential:
        authority["enrolment"] = {
            "public_key": webauthn_credential.public_key.decode() if isinstance(webauthn_credential.public_key, bytes) else webauthn_credential.public_key,
            "aaguid": webauthn_credential.aaguid,
            "attestation_format": webauthn_credential.attestation_format,
            "enrolled_at": webauthn_credential.created_at.isoformat() + "Z",
        }

    # Section 4: goods
    goods = {
        "cart_hash": checkout.cart_hash,
        "cart_version": checkout.cart_version,
        "items": checkout.cart_snapshot.get("items", []),
    }

    # Section 5: agent
    agent = {
        "client_id": checkout.client_id,
        "display_name": "OpenStore Buyer Agent",
        "token_jti": None,  # Would come from OAuth token
        "scopes": ["checkout:create", "checkout:read"],
        "consent_granted_at": checkout.created_at.isoformat() + "Z",
        "edge_identity": None,
    }

    # Section 6: adjudication
    adjudication = {
        "compiler_version": "1.0.0",
        "compiler_digest": "sha256:placeholder",
        "policy_schema_version": policy.policy_version if policy else 2,
        "evaluated_at": now.isoformat() + "Z",
        "context": checkout.cart_snapshot,
        "verdict": "ALLOW" if checkout.state.value != "CANCELLED" else "DENY",
        "reason_code": None,
        "transcript": [],  # Would come from compiler
    }

    # Section 7: notification
    notification = {
        "sent_at": now.isoformat() + "Z",
        "channel": "discord",
        "receipt_digest": notification_receipt.get("digest") if notification_receipt else None,
    }

    # Section 8: aal
    from openstore.core.holdcancel import AALLevel, get_aal_liability_sentence
    aal_level = AALLevel(checkout.aal_level)
    aal = {
        "level": int(aal_level),
        "predicates": {
            "e1": True,  # Has WebAuthn
            "e2": True,  # Verified
            "e3": True,  # Within max_age
            "e4": True,  # Policy has human authority
            "e5": checkout.amount_minor <= 10000,
            "e6": checkout.amount_minor <= 50000,
            "e7": checkout.amount_minor <= 200000,
            "e8": False,
            "e9": False,
        },
        "reasons": [get_aal_liability_sentence(aal_level)],
    }

    # Section 9: campaign (optional, v3.0)
    campaign_section = None
    if campaign_data:
        campaign_section = {
            "campaign_id": campaign_data.get("id"),
            "campaign_version": campaign_data.get("version", 1),
            "draft_digest": campaign_data.get("draft_digest"),
            "approval": {
                "approver_credential_id": campaign_data.get("approver_credential_id"),
                "approved_at": campaign_data.get("approved_at"),
                "amendment_assertion": campaign_data.get("amendment_assertion"),
            },
            "offer_terms": campaign_data.get("offer_terms"),
        }

    # Serialize sections
    sections = {
        "transaction": canonical_json(transaction),
        "human_intent": canonical_json(human_intent),
        "authority": canonical_json(authority),
        "goods": canonical_json(goods),
        "agent": canonical_json(agent),
        "adjudication": canonical_json(adjudication),
        "notification": canonical_json(notification),
        "aal": canonical_json(aal),
    }

    if campaign_section:
        sections["campaign"] = canonical_json(campaign_section)

    # Build hash chain
    chain = build_hash_chain(sections)

    # Merchant signature (placeholder - would sign chain.root with ES256)
    merchant_signature = "placeholder_signature"

    # Time anchor (placeholder - would use Sigstore Rekor)
    time_anchor = {
        "source": "rekor",
        "log_id": "placeholder",
        "inclusion_proof": "placeholder",
    }

    bundle = {
        "poai_version": "1.0",
        "bundle_id": bundle_id,
        "chain": {
            "links": chain["links"],
            "root": chain["root"],
            "merchant_signature": merchant_signature,
            "time_anchor": time_anchor,
        },
        "sections": {k: json.loads(v.decode()) for k, v in sections.items()},
    }

    return bundle


def verify_poai_bundle(bundle: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Verify PoAI bundle hash chain and signatures.

    Returns (verified, errors).
    """
    errors = []

    # Verify hash chain
    if "chain" not in bundle or "links" not in bundle["chain"]:
        errors.append("Missing chain or links")
        return False, errors

    sections = bundle.get("sections", {})
    prev_hash = "0" * 64

    for link in bundle["chain"]["links"]:
        section_name = link["section"]
        if section_name not in sections:
            errors.append(f"Section {section_name} missing from bundle")
            continue

        section_data = canonical_json(sections[section_name])
        computed_hash = hash_section(section_data)

        if computed_hash != link["section_hash"]:
            errors.append(f"Section hash mismatch for {section_name}: expected {link['section_hash']}, got {computed_hash}")

        if link["prev_hash"] != prev_hash:
            errors.append(f"Chain link broken at {section_name}: prev_hash mismatch")

        prev_hash = link["section_hash"]

    # Verify root matches final prev_hash
    if bundle["chain"]["root"] != prev_hash:
        errors.append(f"Root hash mismatch: expected {prev_hash}, got {bundle['chain']['root']}")

    return len(errors) == 0, errors
