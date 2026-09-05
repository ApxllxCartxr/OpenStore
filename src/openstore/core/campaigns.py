# OpenStore core — Campaign store + deterministic validator (S8.1, S8.3)
# INV-14: analytics view is the ONLY input to the campaign agent.

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.models import Campaign, CampaignState
from openstore.notifier import sync_merchant_trace


class CampaignValidationError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


# Symbol/token patterns matched on the raw (lowercased) text — punctuation matters.
_PROMPT_INJECTION_PATTERNS = [
    "system:",
    "system ",  # classic system-prompt override
    "<|",
    "|>",  # LLM special tokens (ChatML, Llama, etc.)
    "[INST",
    "[/INST",  # Llama instruction tags
    "[SYS",
    "[/SYS]",  # Mistral system tags
    "{{",
    "}}",  # Template injection (Handlebars, Jinja2)
    "you are now a",
    "roleplay as",
]

# Q-009 RESOLUTION: full synonym phrase family, matched on normalized text
# (lowercase, whitespace collapsed to single space, non-alphanumerics stripped).
_PROMPT_INJECTION_PHRASES = [
    "ignore previous instructions",
    "ignore all previous",
    "ignore all previous instructions",
    "ignore all prior instructions",
    "ignore all instructions",
    "disregard your instructions",
    "disregard all previous instructions",
    "disregard all prior instructions",
    "disregard all instructions",
    "forget all previous instructions",
    "forget all prior instructions",
    "override your instructions",
    "override your previous instructions",
]


def _normalize_plaintext(text: str) -> str:
    import re

    collapsed = re.sub(r"\s+", " ", text.lower())
    return re.sub(r"[^a-z0-9 ]", "", collapsed)


def _has_prompt_injection(text: str) -> bool:
    lowered = text.lower()
    if any(pat.lower() in lowered for pat in _PROMPT_INJECTION_PATTERNS):
        return True
    normalized = _normalize_plaintext(text)
    return any(phrase in normalized for phrase in _PROMPT_INJECTION_PHRASES)


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

    active_policies = list(
        session.exec(
            select(IntentPolicy).where(IntentPolicy.is_active.is_(True))  # type: ignore
        ).all()
    )

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

    results = list(session.exec(select(Checkout).where(Checkout.merchant_id == merchant_id)).all())

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
        1 for c in results if c.created_at is not None and c.created_at.timestamp() >= cutoff_30d
    )
    for sku_data in sku_stats.values():
        sku_data["attach_rate"] = sku_data["units_sold_30d"] / total_30d if total_30d > 0 else 0.0

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


def compute_merchant_headroom(session: Session, merchant_id: str) -> int:
    """PRD §9.2 stage 1: 'current policy headroom' — the unspent total across
    this merchant's active signed policies. Promoted from
    agents/campaign_agent.py's private _policy_headroom_minor (S14): it now
    has a second caller (MerchantBot's "what's my exposure" report), so it
    belongs alongside the other merchant-wide reporting functions here
    rather than staying private to the campaign drafting flow."""
    from openstore.core.database import compute_policy_exposure
    from openstore.models import IntentPolicy

    policies = list(
        session.exec(
            select(IntentPolicy).where(
                IntentPolicy.merchant_id == merchant_id,
                IntentPolicy.is_active.is_(True),  # type: ignore[attr-defined]
            )
        ).all()
    )
    headroom = 0
    for policy in policies:
        spent = compute_policy_exposure(session, policy.id)
        headroom += max(0, policy.max_spend_total_minor - spent)
    return headroom


def detect_stalled_skus(analytics: list[dict[str, Any]], min_units_30d: int) -> list[str]:
    """DECISION-034: a SKU is "stalled" when it had real, sustained demand in
    the last 30 days (>= min_units_30d — filters out one-off sales, keeps the
    signal meaningful) but sold ZERO units in the last 7. Deterministic,
    Python-side (R0.5) — the LLM never decides what counts as a decline, it
    only drafts a response to one Python already found."""
    return [
        a["sku"]
        for a in analytics
        if a["units_sold_30d"] >= min_units_30d and a["units_sold_7d"] == 0
    ]


def compute_campaign_outcome(
    session: Session, campaign: Campaign, now: datetime | None = None
) -> dict[str, Any]:
    """DECISION-034: the feedback half of the growth loop. Compares units sold
    (across campaign.applies_to_skus) in the campaign's own live window so far
    against an equal-length window immediately BEFORE it started — the same
    SKUs, the same season, half the time offset. No new schema: derived
    entirely from existing Checkout rows, the same source get_analytics_view
    already reads.

    Returns {"units_before": int, "units_after": int, "delta_pct": float | None}.
    delta_pct is None when units_before is 0 (division is meaningless, not
    zero — a SKU that sold nothing before a campaign and something during it
    saw an infinite, not a 0%, lift)."""
    from openstore.models import Checkout

    clock = (now or datetime.now(UTC)).replace(tzinfo=None)
    starts_at = campaign.starts_at
    window_end = min(clock, campaign.ends_at)
    window_seconds = (window_end - starts_at).total_seconds()
    before_start = starts_at - (window_end - starts_at)

    skus = set(campaign.applies_to_skus)
    units_before = 0
    units_after = 0
    if window_seconds > 0 and skus:
        checkouts = list(
            session.exec(
                select(Checkout).where(
                    Checkout.merchant_id == campaign.merchant_id,
                    Checkout.created_at >= before_start,
                    Checkout.created_at <= window_end,
                )
            ).all()
        )
        for checkout in checkouts:
            if checkout.created_at is None:
                continue
            for item in checkout.cart_snapshot.get("items", []):
                if item.get("sku") not in skus:
                    continue
                qty = item.get("qty", 1)
                if checkout.created_at < starts_at:
                    units_before += qty
                else:
                    units_after += qty

    delta_pct = None if units_before == 0 else (units_after - units_before) / units_before * 100
    return {"units_before": units_before, "units_after": units_after, "delta_pct": delta_pct}


AUTO_TRIGGER_SOURCE = "auto_stall_detected"


def should_auto_trigger(
    session: Session, config: Settings, merchant_id: str, now: datetime | None = None
) -> list[str]:
    """DECISION-034: returns the stalled SKUs worth drafting a campaign for
    right now, or [] to mean "don't". Empty covers three distinct reasons,
    all by design rather than accident:
      - nothing is actually stalled (detect_stalled_skus found nothing)
      - every stalled SKU is already covered by a live campaign (ACTIVE or
        PENDING_APPROVAL — no point drafting a second offer for the same SKU)
      - the merchant had an auto-triggered draft within the cooldown window,
        regardless of which SKUs it covered (bounds draft frequency
        independent of how many SKUs stall at once, R0.5)
    """
    clock = (now or datetime.now(UTC)).replace(tzinfo=None)

    cooldown_cutoff = clock - timedelta(hours=config.campaign.auto_trigger_cooldown_hours)
    recent_auto = list(
        session.exec(
            select(Campaign).where(
                Campaign.merchant_id == merchant_id,
                Campaign.created_at >= cooldown_cutoff,
            )
        ).all()
    )
    if any(c.source_signals.get("trigger") == AUTO_TRIGGER_SOURCE for c in recent_auto):
        return []

    analytics = get_analytics_view(session, merchant_id)
    stalled = detect_stalled_skus(analytics, config.campaign.stall_min_units_30d)
    if not stalled:
        return []

    live = list(
        session.exec(
            select(Campaign).where(
                Campaign.merchant_id == merchant_id,
                Campaign.state.in_(  # type: ignore[attr-defined]
                    [CampaignState.ACTIVE, CampaignState.PENDING_APPROVAL]
                ),
            )
        ).all()
    )
    already_covered = {sku for c in live for sku in c.applies_to_skus}
    return [sku for sku in stalled if sku not in already_covered]


def recent_campaign_outcomes(
    session: Session, merchant_id: str, limit: int = 3
) -> list[dict[str, Any]]:
    """DECISION-034: feeds CampaignAgent's prompt with whether past campaigns
    actually worked, so a repeated suggestion is informed by history instead
    of re-running the same play blind. Only campaigns with a real elapsed
    window (ACTIVE, PAUSED, EXPIRED — never DRAFT/PENDING_APPROVAL/REJECTED,
    which never ran) are eligible."""
    live_states = [CampaignState.ACTIVE, CampaignState.PAUSED, CampaignState.EXPIRED]
    campaigns = list(
        session.exec(
            select(Campaign)
            .where(Campaign.merchant_id == merchant_id, Campaign.state.in_(live_states))  # type: ignore[attr-defined]
            .order_by(Campaign.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
    )
    outcomes = []
    for c in campaigns:
        outcome = compute_campaign_outcome(session, c)
        outcomes.append(
            {
                "title": c.title,
                "discount_bps": c.discount_bps,
                "applies_to_skus": c.applies_to_skus,
                **outcome,
            }
        )
    return outcomes


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
    source_signals: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> Campaign:
    """Create a DRAFT campaign (S8.2).

    DECISION-025: the draft is run through validate_campaign() BEFORE it reaches
    the DB. Previously nothing in src/ called the validator at all, so R0.9's
    "the LLM proposes, Python disposes" had no disposer on the only write path —
    an out-of-bounds discount or an injected rationale would persist and only be
    caught, if ever, at approval time.
    """
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
        source_signals=source_signals or {},
        draft_digest=draft_digest,
        state=CampaignState.DRAFT,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    validate_campaign(session, campaign, config)

    session.add(campaign)
    session.flush()
    _trace(trace_id, "campaign_drafted", campaign)
    return campaign


def _trace(trace_id: str | None, action: str, campaign: Campaign, **extra: Any) -> None:
    """PRD §9.4: every campaign transition is logged to #merchant-trace with its
    trace_id. sync_merchant_trace is the log-only shim (never network), so this
    is safe to call from core/."""
    sync_merchant_trace(
        trace_id or "campaign",
        action,
        {
            "campaign_id": campaign.id,
            "merchant_id": campaign.merchant_id,
            "state": campaign.state.value,
            **extra,
        },
    )


def _require(session: Session, campaign_id: str) -> Campaign:
    campaign = session.exec(select(Campaign).where(Campaign.id == campaign_id)).first()
    if not campaign:
        raise CampaignValidationError("campaign.not_found", f"Campaign {campaign_id} not found")
    return campaign


def submit_for_approval(
    session: Session, campaign_id: str, trace_id: str | None = None
) -> Campaign:
    """DRAFT -> PENDING_APPROVAL (PRD §9.4).

    DECISION-025: this transition had no implementation, so PENDING_APPROVAL was
    unreachable — and the Campaign Studio renders its Approve/Reject controls only
    for that state, which is why the Studio could never show an actionable campaign.
    """
    campaign = _require(session, campaign_id)
    if campaign.state != CampaignState.DRAFT:
        raise CampaignValidationError(
            "campaign.invalid_state_transition",
            f"Campaign {campaign_id} is {campaign.state.value}, not DRAFT",
        )
    campaign.state = CampaignState.PENDING_APPROVAL
    campaign.updated_at = datetime.now(UTC)
    session.add(campaign)
    session.flush()
    _trace(trace_id, "campaign_submitted", campaign)
    return campaign


def activate_campaign(
    session: Session,
    campaign_id: str,
    approver_credential_id: str,
    webauthn_assertion: dict[str, Any] | None = None,
    *,
    config: Settings | None = None,
    user_handle: str | None = None,
    challenge_store: Any | None = None,
    trace_id: str | None = None,
) -> Campaign:
    """S8.4: approve and publish a campaign. PRD §9.7 — the orchestrator CANNOT
    publish without a WebAuthn approval.

    DECISION-024 (SECURITY): this previously accepted any truthy assertion dict
    and never invoked the RP, so `{"x": 1}` was enough to move a campaign ACTIVE
    and publish a signed, agent-discoverable offer. The assertion is now verified
    against the RP with binding {"mode": "campaign", "campaign_id": <id>} — the
    campaign_id inside the binding is what stops an approval for campaign A being
    replayed onto campaign B.

    `config` and `user_handle` are keyword-only and optional purely so the
    pre-existing call shape still type-checks; both are REQUIRED for the
    verification to run, and their absence is a hard error (R0.5), never a skip.
    """
    campaign = _require(session, campaign_id)

    if campaign.state != CampaignState.PENDING_APPROVAL:
        raise CampaignValidationError(
            "campaign.invalid_state_transition",
            f"Campaign {campaign_id} is {campaign.state.value}, not PENDING_APPROVAL",
        )

    if not webauthn_assertion:
        raise CampaignValidationError(
            "campaign.no_webauthn_approval",
            "Campaign activation requires a valid WebAuthn assertion",
        )

    if config is None or user_handle is None:
        raise CampaignValidationError(
            "campaign.no_webauthn_approval",
            "Campaign activation requires config and user_handle to verify the assertion",
        )

    _verify_approval_assertion(
        session,
        config,
        user_handle,
        campaign_id,
        approver_credential_id,
        webauthn_assertion,
        challenge_store,
    )

    max_active = config.campaign.max_active
    active_count = len(
        list(
            session.exec(
                select(Campaign).where(
                    Campaign.state == CampaignState.ACTIVE,
                    Campaign.merchant_id == campaign.merchant_id,
                )
            ).all()
        )
    )
    if active_count >= max_active:
        raise CampaignValidationError(
            "campaign.max_active_exceeded",
            f"Max active campaigns ({max_active}) already reached for {campaign.merchant_id}",
        )

    campaign.state = CampaignState.ACTIVE
    campaign.approver_credential_id = approver_credential_id
    campaign.approved_at = datetime.now(UTC)
    campaign.webauthn_assertion = webauthn_assertion
    campaign.updated_at = datetime.now(UTC)
    session.add(campaign)
    session.flush()
    _trace(trace_id, "campaign_approved", campaign, approver=approver_credential_id)
    return campaign


def _verify_approval_assertion(
    session: Session,
    config: Settings,
    user_handle: str,
    campaign_id: str,
    credential_id: str,
    assertion: dict[str, Any],
    challenge_store: Any | None,
) -> None:
    """Run the approval assertion through the real RP (DECISION-024)."""
    from openstore.core.webauthn_rp import WebAuthnError, complete_assertion

    missing = [
        k
        for k in ("client_data_json", "authenticator_data", "signature", "challenge")
        if not assertion.get(k)
    ]
    if missing:
        raise CampaignValidationError(
            "campaign.no_webauthn_approval",
            f"Campaign approval assertion is missing {', '.join(missing)}",
        )

    try:
        complete_assertion(
            session,
            config,
            user_handle,
            credential_id,
            assertion["client_data_json"],
            assertion["authenticator_data"],
            assertion["signature"],
            assertion["challenge"],
            binding={"mode": "campaign", "campaign_id": campaign_id},
            store=challenge_store,
        )
    except WebAuthnError as e:
        raise CampaignValidationError(
            "campaign.webauthn_verification_failed",
            f"Campaign approval assertion rejected: {e.reason_code}",
        ) from e


def reject_campaign(
    session: Session, campaign_id: str, reason: str = "", trace_id: str | None = None
) -> Campaign:
    """PENDING_APPROVAL -> REJECTED (PRD §9.4). Terminal."""
    campaign = _require(session, campaign_id)
    if campaign.state not in (CampaignState.DRAFT, CampaignState.PENDING_APPROVAL):
        raise CampaignValidationError(
            "campaign.invalid_state_transition",
            f"Campaign {campaign_id} is {campaign.state.value} and cannot be rejected",
        )
    campaign.state = CampaignState.REJECTED
    campaign.updated_at = datetime.now(UTC)
    session.add(campaign)
    session.flush()
    _trace(trace_id, "campaign_rejected", campaign, reason=reason)
    return campaign


def pause_campaign(session: Session, campaign_id: str, trace_id: str | None = None) -> Campaign:
    """ACTIVE -> PAUSED (PRD §9.4). Reversible; drops the offer out of the feed
    without the finality of REJECTED."""
    campaign = _require(session, campaign_id)
    if campaign.state != CampaignState.ACTIVE:
        raise CampaignValidationError(
            "campaign.invalid_state_transition",
            f"Campaign {campaign_id} is {campaign.state.value}, not ACTIVE",
        )
    campaign.state = CampaignState.PAUSED
    campaign.updated_at = datetime.now(UTC)
    session.add(campaign)
    session.flush()
    _trace(trace_id, "campaign_paused", campaign)
    return campaign


def expire_campaigns_due(
    session: Session, now: datetime | None = None, trace_id: str | None = None
) -> list[Campaign]:
    """PRD §9.4: 'EXPIRED is set by the sweeper when now >= ends_at.'

    Sweeps ACTIVE and PAUSED campaigns past their window. Returns the rows it
    moved so the caller can notify; commits nothing (the caller owns the txn).
    """
    cutoff = (now or datetime.now(UTC)).replace(tzinfo=None)
    due = list(
        session.exec(
            select(Campaign).where(
                Campaign.state.in_([CampaignState.ACTIVE, CampaignState.PAUSED]),  # type: ignore[attr-defined]
                Campaign.ends_at <= cutoff,
            )
        ).all()
    )
    for campaign in due:
        campaign.state = CampaignState.EXPIRED
        campaign.updated_at = datetime.now(UTC)
        session.add(campaign)
        _trace(trace_id, "campaign_expired", campaign)
    if due:
        session.flush()
    return due
