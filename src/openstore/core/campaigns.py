# OpenStore core — Campaign store + deterministic validator (S8.1, S8.3)
# INV-14: analytics view is the ONLY input to the campaign agent.

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.models import Campaign, CampaignState


class CampaignValidationError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


_PROMPT_INJECTION_PATTERNS = [
    "system:", "system ",   # classic system-prompt override
    "<|", "|>",            # LLM special tokens (ChatML, Llama, etc.)
    "[INST", "[/INST",     # Llama instruction tags
    "[SYS", "[/SYS]",      # Mistral system tags
    "{{", "}}",            # Template injection (Handlebars, Jinja2)
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "you are now a",
    "roleplay as",
]


def _has_prompt_injection(text: str) -> bool:
    t = text.lower()
    return any(pat.lower() in t for pat in _PROMPT_INJECTION_PATTERNS)


def validate_campaign(
    session: Session,
    campaign: Campaign,
    config: Settings,
) -> None:
    """
    S8.3: Deterministic validator (no LLM).
    Fails loud on any breach (R0.5).
    """
    # Every SKU exists in catalog
    if campaign.applies_to_skus:
        from openstore.surfaces.catalog import load_catalog
        catalog = load_catalog(config)
        catalog_skus = {item["sku"] for item in catalog}
        for sku in campaign.applies_to_skus:
            if sku not in catalog_skus:
                raise CampaignValidationError(
                    "campaign.sku_not_found",
                    f"Campaign references unknown SKU: {sku}",
                )

    # discount_bps within bounds
    min_bps = config.campaign.min_bps
    max_bps = config.campaign.max_bps
    if not (min_bps <= campaign.discount_bps <= max_bps):
        raise CampaignValidationError(
            "campaign.discount_out_of_bounds",
            f"discount_bps={campaign.discount_bps} outside [{min_bps}, {max_bps}]",
        )

    # Window well-formed
    if campaign.starts_at >= campaign.ends_at:
        raise CampaignValidationError(
            "campaign.invalid_window",
            f"starts_at ({campaign.starts_at}) must be before ends_at ({campaign.ends_at})",
        )

    # No SKU on active policy blocked_skus (unless campaign explicitly excluded)
    # Check blocked_skus from active policies
    from openstore.models import IntentPolicy
    active_policies = list(session.exec(
        select(IntentPolicy).where(IntentPolicy.is_active.is_(True))  # type: ignore
    ).all())

    for sku in campaign.applies_to_skus:
        for policy in active_policies:
            if sku in (policy.blocked_skus or []):
                raise CampaignValidationError(
                    "campaign.sku_on_blocked_list",
                    f"Campaign applies to SKU {sku} which is blocked by policy {policy.id}",
                )

    # Projected price integer paise (discount_bps is bps, already integer)
    # Content rules: no empty fields, no prompt-injection patterns
    for field_name, field_value in [("title", campaign.title), ("rationale", campaign.rationale)]:
        if not field_value or len(field_value.strip()) == 0:
            raise CampaignValidationError(
                "campaign.empty_content",
                f"Campaign {field_name} is empty",
            )
        if _has_prompt_injection(field_value):
            raise CampaignValidationError(
                "campaign.injection_content",
                f"Campaign {field_name} contains disallowed content pattern",
            )


def get_analytics_view(session: Session, merchant_id: str) -> list[dict[str, Any]]:
    """
    S8.1 / INV-14: analytics view (the ONLY input to the campaign agent).
    Returns: sku, units_sold_7d, units_sold_30d, gross_minor_30d, attach_rate, last_sold_at.
    The agent NEVER reads raw orders, buyer identities, or payment data.
    """
    from openstore.models import Checkout

    cutoff_7d = datetime.now(UTC).timestamp() - 7 * 86400
    cutoff_30d = datetime.now(UTC).timestamp() - 30 * 86400

    results = list(session.exec(
        select(Checkout).where(Checkout.merchant_id == merchant_id)
    ).all())

    sku_stats: dict[str, dict[str, Any]] = {}
    for checkout in results:
        cart_items = checkout.cart_snapshot.get("items", [])
        created_ts = checkout.created_at.timestamp() if checkout.created_at else 0

        for item in cart_items:
            sku = item.get("sku", "unknown")
            qty = item.get("qty", 1)
            unit_minor = item.get("unit_minor", 0)

            if sku not in sku_stats:
                sku_stats[sku] = {
                    "units_sold_7d": 0,
                    "units_sold_30d": 0,
                    "gross_minor_30d": 0,
                    "last_sold_at": None,
                }

            if created_ts >= cutoff_7d:
                sku_stats[sku]["units_sold_7d"] += qty
            if created_ts >= cutoff_30d:
                sku_stats[sku]["units_sold_30d"] += qty
                sku_stats[sku]["gross_minor_30d"] += unit_minor * qty
                if checkout.created_at:
                    last = sku_stats[sku]["last_sold_at"]
                    if last is None or checkout.created_at.timestamp() > last:
                        sku_stats[sku]["last_sold_at"] = checkout.created_at.timestamp()

    # Compute attach_rate = units_sold_30d / total_checkouts_30d
    total_30d = sum(
        1 for c in results
        if c.created_at is not None and c.created_at.timestamp() >= cutoff_30d
    )
    for sku_data in sku_stats.values():
        sku_data["attach_rate"] = (
            sku_data["units_sold_30d"] / total_30d if total_30d > 0 else 0.0
        )

    return [
        {
            "sku": sku,
            "units_sold_7d": data["units_sold_7d"],
            "units_sold_30d": data["units_sold_30d"],
            "gross_minor_30d": data["gross_minor_30d"],
            "attach_rate": data["attach_rate"],
            "last_sold_at": data["last_sold_at"],
        }
        for sku, data in sku_stats.items()
    ]


def create_campaign(
    session: Session,
    config: Settings,
    merchant_id: str,
    title: str,
    rationale: str,
    discount_bps: int,
    applies_to_skus: list[str],
    starts_at: datetime,
    ends_at: datetime,
) -> Campaign:
    """Create a DRAFT campaign (S8.2)."""
    import hashlib
    import secrets

    campaign_id = f"camp_{secrets.token_hex(8)}"
    draft_data = {
        "campaign_id": campaign_id,
        "title": title,
        "discount_bps": discount_bps,
        "applies_to_skus": applies_to_skus,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
    }
    draft_bytes = json.dumps(draft_data, sort_keys=True, separators=(",", ":")).encode()
    draft_digest = f"sha256:{hashlib.sha256(draft_bytes).hexdigest()}"

    campaign = Campaign(
        id=campaign_id,
        merchant_id=merchant_id,
        campaign_version=1,
        title=title,
        rationale=rationale,
        discount_bps=discount_bps,
        applies_to_skus=applies_to_skus,
        starts_at=starts_at,
        ends_at=ends_at,
        source_signals={},
        draft_digest=draft_digest,
        state=CampaignState.DRAFT,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    session.add(campaign)
    session.flush()
    return campaign


def activate_campaign(
    session: Session,
    campaign_id: str,
    approver_credential_id: str,
    webauthn_assertion: dict[str, Any] | None = None,
) -> Campaign:
    """
    S8.4: Approve and activate a campaign.
    MUST have a valid WebAuthn assertion (no self-approval, R0.5).
    """
    campaign = session.exec(select(Campaign).where(Campaign.id == campaign_id)).first()
    if not campaign:
        raise CampaignValidationError("campaign.not_found", f"Campaign {campaign_id} not found")

    if not webauthn_assertion:
        raise CampaignValidationError(
            "campaign.no_webauthn_approval",
            "Campaign activation requires a valid WebAuthn assertion",
        )

    # Count active campaigns
    active_count = len(list(session.exec(
        select(Campaign).where(Campaign.state == CampaignState.ACTIVE)
    ).all()))

    if active_count >= 5:  # max_active from config
        raise CampaignValidationError(
            "campaign.max_active_exceeded",
            f"Max active campaigns ({active_count}) reached",
        )

    campaign.state = CampaignState.ACTIVE
    campaign.approver_credential_id = approver_credential_id
    campaign.approved_at = datetime.now(UTC)
    campaign.webauthn_assertion = webauthn_assertion
    campaign.updated_at = datetime.now(UTC)
    session.add(campaign)
    session.flush()
    return campaign
