# OpenStore core — merchandising rules (S27: cross-sell and up-sell).
#
# Selection is LLM-free and fully deterministic: ACTIVE rules first, then
# adapter-native related fields, then catalog related_skus — in-cart SKUs
# excluded, deduped, stage-26 sellability-filtered, capped. An LLM may word
# the surface copy and nothing else. The same cart against the same rules
# and stock yields the same ordered list (pinned by a golden test).
#
# INV-14 preserved: drafting reads ONLY get_analytics_view plus aggregate
# co-occurrence counts — the agent never sees raw orders, buyer identities,
# or payment data. A draft takes effect solely on a passkey assertion bound
# to {"mode": "merchandising", "rule_id": ...} (replay across rules fails
# closed, DECISION-024 binding precedent).
#
# Session discipline (plan risk #1): every helper takes the CALLER's session.

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from openstore.config import Settings
from openstore.models import Campaign, CampaignState, MerchandisingKind, MerchandisingRule
from openstore.notifier import sync_merchant_trace


class MerchandisingError(Exception):
    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"[{reason_code}] {message}")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _trace(trace_id: str | None, action: str, rule: MerchandisingRule, **extra: Any) -> None:
    sync_merchant_trace(
        trace_id or "merchandising",
        action,
        {
            "rule_id": rule.id,
            "merchant_id": rule.merchant_id,
            "kind": rule.kind.value,
            "state": rule.state.value,
            **extra,
        },
    )


def _require(session: Session, rule_id: str) -> MerchandisingRule:
    rule = session.exec(select(MerchandisingRule).where(MerchandisingRule.id == rule_id)).first()
    if not rule:
        raise MerchandisingError("merchandising.not_found", f"Rule {rule_id} not found")
    return rule


def _digest(rule_id: str, kind: str, triggers: list[str], suggested: str) -> str:
    raw = json.dumps(
        {"rule_id": rule_id, "kind": kind, "triggers": sorted(triggers),
         "suggested": suggested},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


# ---------------------------------------------------------------------------
# Lifecycle (mirrors core/campaigns.py: draft -> validate -> DRAFT ->
# PENDING_APPROVAL -> ACTIVE -> PAUSED / EXPIRED; REJECTED terminal).
# ---------------------------------------------------------------------------


def create_rule(
    session: Session,
    config: Settings,
    merchant_id: str,
    kind: MerchandisingKind,
    title: str,
    rationale: str,
    trigger_skus: list[str],
    suggested_sku: str,
    campaign_id: str | None = None,
    source_signals: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> MerchandisingRule:
    """Author a DRAFT rule (merchant browser form or agent draft — both land
    here, and neither takes effect without the passkey ceremony). Validates
    deterministically before anything reaches the DB (R0.9)."""
    from openstore.surfaces.catalog import load_catalog

    # Thread the caller's session (settings-overlay rule — see
    # core/campaigns.py::validate_campaign).
    catalog = {i["sku"]: i for i in load_catalog(config, session)}
    unknown = [s for s in [*trigger_skus, suggested_sku] if s not in catalog]
    if unknown:
        raise MerchandisingError(
            "catalog.sku_not_found",
            f"Rule references unknown SKU(s): {', '.join(sorted(set(unknown)))}",
        )
    if not trigger_skus:
        raise MerchandisingError("merchandising.invalid_rule", "trigger_skus must not be empty")
    if suggested_sku in trigger_skus:
        raise MerchandisingError(
            "merchandising.invalid_rule",
            f"suggested_sku {suggested_sku!r} must not be one of its own triggers",
        )
    if kind == MerchandisingKind.BUNDLE:
        if not campaign_id:
            raise MerchandisingError(
                "merchandising.invalid_rule",
                "BUNDLE rules reference a Campaign for their discount (DECISION-048)",
            )
        campaign = session.exec(select(Campaign).where(Campaign.id == campaign_id)).first()
        if campaign is None:
            raise MerchandisingError(
                "campaign.not_found", f"Campaign {campaign_id} not found"
            )
    elif campaign_id is not None:
        raise MerchandisingError(
            "merchandising.invalid_rule",
            f"Only BUNDLE rules carry a campaign_id, not {kind.value}",
        )
    if not title.strip() or not rationale.strip():
        raise MerchandisingError("merchandising.invalid_rule", "title/rationale must not be empty")

    rule_id = f"mrule_{secrets.token_hex(8)}"
    rule = MerchandisingRule(
        id=rule_id,
        merchant_id=merchant_id,
        kind=kind,
        title=title,
        rationale=rationale,
        trigger_skus=list(trigger_skus),
        suggested_sku=suggested_sku,
        campaign_id=campaign_id,
        source_signals=source_signals or {},
        draft_digest=_digest(rule_id, kind.value, list(trigger_skus), suggested_sku),
        state=CampaignState.DRAFT,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(rule)
    session.flush()
    _trace(trace_id, "rule_drafted", rule)
    return rule


def submit_for_approval(
    session: Session, rule_id: str, trace_id: str | None = None
) -> MerchandisingRule:
    """DRAFT -> PENDING_APPROVAL."""
    rule = _require(session, rule_id)
    if rule.state != CampaignState.DRAFT:
        raise MerchandisingError(
            "merchandising.invalid_state_transition",
            f"Rule {rule_id} is {rule.state.value}, not DRAFT",
        )
    rule.state = CampaignState.PENDING_APPROVAL
    rule.updated_at = _now()
    session.add(rule)
    session.flush()
    _trace(trace_id, "rule_submitted", rule)
    return rule


def activate_rule(
    session: Session,
    rule_id: str,
    approver_credential_id: str,
    webauthn_assertion: dict[str, Any] | None = None,
    *,
    config: Settings | None = None,
    user_handle: str | None = None,
    challenge_store: Any | None = None,
    trace_id: str | None = None,
) -> MerchandisingRule:
    """PENDING_APPROVAL -> ACTIVE on a verified passkey assertion bound to
    {"mode": "merchandising", "rule_id": <id>} — an assertion for one rule
    fails closed on another (DECISION-024 replay-binding precedent)."""
    rule = _require(session, rule_id)
    if rule.state != CampaignState.PENDING_APPROVAL:
        raise MerchandisingError(
            "merchandising.invalid_state_transition",
            f"Rule {rule_id} is {rule.state.value}, not PENDING_APPROVAL",
        )
    if not webauthn_assertion:
        raise MerchandisingError(
            "merchandising.no_webauthn_approval",
            "Rule activation requires a valid WebAuthn assertion",
        )
    if config is None or user_handle is None:
        raise MerchandisingError(
            "merchandising.no_webauthn_approval",
            "Rule activation requires config and user_handle to verify the assertion",
        )
    _verify_approval_assertion(
        session, config, user_handle, rule_id,
        approver_credential_id, webauthn_assertion, challenge_store,
    )
    rule.state = CampaignState.ACTIVE
    rule.approver_credential_id = approver_credential_id
    rule.approved_at = _now()
    rule.webauthn_assertion = webauthn_assertion
    rule.updated_at = _now()
    session.add(rule)
    session.flush()
    _trace(trace_id, "rule_approved", rule, approver=approver_credential_id)
    return rule


def _verify_approval_assertion(
    session: Session,
    config: Settings,
    user_handle: str,
    rule_id: str,
    credential_id: str,
    assertion: dict[str, Any],
    challenge_store: Any | None,
) -> None:
    from openstore.core.webauthn_rp import WebAuthnError, complete_assertion

    missing = [
        k
        for k in ("client_data_json", "authenticator_data", "signature", "challenge")
        if not assertion.get(k)
    ]
    if missing:
        raise MerchandisingError(
            "merchandising.no_webauthn_approval",
            f"Rule approval assertion is missing {', '.join(missing)}",
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
            binding={"mode": "merchandising", "rule_id": rule_id},
            store=challenge_store,
        )
    except WebAuthnError as e:
        raise MerchandisingError(
            "merchandising.webauthn_verification_failed",
            f"Rule approval assertion rejected: {e.reason_code}",
        ) from e


def reject_rule(
    session: Session, rule_id: str, reason: str = "", trace_id: str | None = None
) -> MerchandisingRule:
    """DRAFT / PENDING_APPROVAL -> REJECTED. Terminal."""
    rule = _require(session, rule_id)
    if rule.state not in (CampaignState.DRAFT, CampaignState.PENDING_APPROVAL):
        raise MerchandisingError(
            "merchandising.invalid_state_transition",
            f"Rule {rule_id} is {rule.state.value} and cannot be rejected",
        )
    rule.state = CampaignState.REJECTED
    rule.updated_at = _now()
    session.add(rule)
    session.flush()
    _trace(trace_id, "rule_rejected", rule, reason=reason)
    return rule


def pause_rule(
    session: Session, rule_id: str, trace_id: str | None = None
) -> MerchandisingRule:
    """ACTIVE -> PAUSED. Reversible; a paused BUNDLE is silently inapplicable
    rather than an error (DECISION-048)."""
    rule = _require(session, rule_id)
    if rule.state != CampaignState.ACTIVE:
        raise MerchandisingError(
            "merchandising.invalid_state_transition",
            f"Rule {rule_id} is {rule.state.value}, not ACTIVE",
        )
    rule.state = CampaignState.PAUSED
    rule.updated_at = _now()
    session.add(rule)
    session.flush()
    _trace(trace_id, "rule_paused", rule)
    return rule


# ---------------------------------------------------------------------------
# Deterministic suggestion (LLM-free selection; an LLM may word copy only).
# ---------------------------------------------------------------------------

_SUGGESTION_CAP_DEFAULT = 4


def suggest_for_cart(
    session: Session,
    config: Settings,
    merchant_id: str,
    cart_skus: list[str],
    limit: int = _SUGGESTION_CAP_DEFAULT,
) -> list[dict[str, Any]]:
    """Ordered suggestions for a cart: ACTIVE rules first, then
    adapter-native related fields, then catalog related_skus. In-cart SKUs
    excluded, deduped, stage-26 sellability-filtered, capped. Order is stable
    (rules by (created_at, id); adapter/native by first-seen cart order) and
    pinned by a golden test.

    Each suggestion: {sku, name, unit_minor, source, rule_id?, kind?,
    campaign_id?, price_delta_minor?, why}. UPGRADE carries an honest price
    delta and never mutates the cart. BUNDLE carries its Campaign's id; the
    discount itself is recomputed by compiler check 12 at checkout
    (DECISION-048) — a paused/expired/non-ACTIVE campaign makes the rule
    silently inapplicable, never an error.
    """
    from openstore.core.inventory import is_sellable
    from openstore.surfaces.catalog import load_catalog

    catalog = {i["sku"]: i for i in load_catalog(config, session)}
    cart_set = set(cart_skus)
    suggestions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _admit(sku: str, suggestion: dict[str, Any]) -> None:
        if sku in cart_set or sku in seen:
            return
        item = catalog.get(sku)
        if item is None:
            return  # hallucinated SKU: never suggest (R0.3)
        if not is_sellable(session, merchant_id, sku):
            return  # out of stock: never suggest
        seen.add(sku)
        suggestions.append(
            {
                "sku": sku,
                "name": item["name"],
                "unit_minor": int(item["unit_minor"]),
                **suggestion,
            }
        )

    for rule in _active_rules(session, merchant_id):
        if len(suggestions) >= limit:
            break
        if not (set(rule.trigger_skus) & cart_set):
            continue
        if rule.kind == MerchandisingKind.UPGRADE:
            target = catalog.get(rule.suggested_sku)
            trigger_units = [
                catalog[t]["unit_minor"] for t in rule.trigger_skus
                if t in cart_set and t in catalog
            ]
            base = min(trigger_units) if trigger_units else None
            delta = (int(target["unit_minor"]) - base) if target and base is not None else None
            _admit(rule.suggested_sku, {
                "source": "rule",
                "rule_id": rule.id,
                "kind": rule.kind.value,
                "price_delta_minor": delta,
                "why": f"upgrade from {sorted(set(rule.trigger_skus) & cart_set)}",
            })
        elif rule.kind == MerchandisingKind.BUNDLE:
            campaign = (
                session.exec(select(Campaign).where(Campaign.id == rule.campaign_id)).first()
                if rule.campaign_id
                else None
            )
            if campaign is None or campaign.state != CampaignState.ACTIVE:
                continue  # silently inapplicable (DECISION-048)
            _admit(rule.suggested_sku, {
                "source": "rule",
                "rule_id": rule.id,
                "kind": rule.kind.value,
                "campaign_id": campaign.id,
                "discount_bps": campaign.discount_bps,
                "why": f"bundle with {campaign.title}",
            })
        else:
            _admit(rule.suggested_sku, {
                "source": "rule",
                "rule_id": rule.id,
                "kind": rule.kind.value,
                "why": f"bought together with {sorted(set(rule.trigger_skus) & cart_set)}",
            })

    for sku in cart_skus:
        if len(suggestions) >= limit:
            break
        item = catalog.get(sku)
        if not item:
            continue
        for related in item.get("related_skus", []):
            if len(suggestions) >= limit:
                break
            _admit(related, {
                "source": "adapter" if item.get("related_source", "yaml") != "yaml" else "related",
                "why": f"related to {sku}",
            })

    return suggestions[:limit]


def _active_rules(session: Session, merchant_id: str) -> list[MerchandisingRule]:
    rules = list(
        session.exec(
            select(MerchandisingRule).where(
                MerchandisingRule.merchant_id == merchant_id,
                MerchandisingRule.state == CampaignState.ACTIVE,
            )
        ).all()
    )
    rules.sort(key=lambda r: (r.created_at, r.id))
    return rules


# ---------------------------------------------------------------------------
# Drafting from aggregates only (INV-14): analytics view + co-occurrence.
# ---------------------------------------------------------------------------


def co_occurrence_counts(
    session: Session, merchant_id: str
) -> dict[tuple[str, str], int]:
    """Aggregate co-occurrence: how many checkouts contained both SKUs (order
    canonicalised, self-pairs excluded). Counts only — no buyer identity, no
    payment data, no raw orders leave this function (INV-14)."""
    from openstore.models import Checkout

    pair_counts: dict[tuple[str, str], int] = {}
    checkouts = session.exec(
        select(Checkout).where(Checkout.merchant_id == merchant_id)
    ).all()
    for checkout in checkouts:
        skus = sorted({i.get("sku", "") for i in (checkout.cart_snapshot or {}).get("items", [])})
        skus = [s for s in skus if s]
        for a_pos in range(len(skus)):
            for b_pos in range(a_pos + 1, len(skus)):
                pair = (skus[a_pos], skus[b_pos])
                pair_counts[pair] = pair_counts.get(pair, 0) + 1
    return pair_counts


def draft_merchandising_rules(
    session: Session,
    config: Settings,
    merchant_id: str,
    limit: int = 5,
    trace_id: str | None = None,
) -> list[MerchandisingRule]:
    """Draft up to `limit` CROSS_SELL rules from aggregate co-occurrence
    (pairs seen together in >= 2 checkouts), then UPGRADE drafts for stalled
    SKUs whose higher-priced same-tag sibling co-occurs. Deterministic
    (sorted by (-count, pair)); DRAFT state — activation needs the passkey
    ceremony, exactly like campaigns. BUNDLE rules are merchant-authored only
    (they name a Campaign; inventing one from aggregates would pick someone
    else's discount     terms)."""
    from openstore.surfaces.catalog import load_catalog

    catalog = {i["sku"]: i for i in load_catalog(config, session)}
    pairs = sorted(co_occurrence_counts(session, merchant_id).items(),
                   key=lambda kv: (-kv[1], kv[0]))
    drafts: list[MerchandisingRule] = []

    def _exists(trigger: str, suggested: str, kind: MerchandisingKind) -> bool:
        rows = session.exec(
            select(MerchandisingRule).where(
                MerchandisingRule.merchant_id == merchant_id,
                MerchandisingRule.kind == kind,
                MerchandisingRule.suggested_sku == suggested,
            )
        ).all()
        return any(trigger in (r.trigger_skus or []) for r in rows)

    for (a, b), count in pairs:
        if len(drafts) >= limit or count < 2:
            break
        if a not in catalog or b not in catalog:
            continue
        for trigger, suggested in ((a, b), (b, a)):
            if len(drafts) >= limit:
                break
            if _exists(trigger, suggested, MerchandisingKind.CROSS_SELL):
                continue
            drafts.append(
                create_rule(
                    session, config, merchant_id,
                    MerchandisingKind.CROSS_SELL,
                    title=f"Pairs with {catalog[trigger]['name']}",
                    rationale=(
                        f"Bought together in {count} checkouts "
                        f"(aggregate co-occurrence, INV-14)."
                    ),
                    trigger_skus=[trigger],
                    suggested_sku=suggested,
                    source_signals={"trigger": "auto_co_occurrence",
                                    "together_count": count},
                    trace_id=trace_id,
                )
            )

    if len(drafts) < limit:
        for (a, _b), count in pairs:
            if len(drafts) >= limit:
                break
            for trigger, sibling in ((a, _b), (_b, a)):
                cand = catalog.get(sibling)
                trig = catalog.get(trigger)
                if not cand or not trig:
                    continue
                if int(cand["unit_minor"]) <= int(trig["unit_minor"]):
                    continue
                if not (set(cand.get("tags", [])) & set(trig.get("tags", []))):
                    continue
                if _exists(trigger, sibling, MerchandisingKind.UPGRADE):
                    continue
                drafts.append(
                    create_rule(
                        session, config, merchant_id,
                        MerchandisingKind.UPGRADE,
                        title=f"Upgrade from {trig['name']}",
                        rationale=(
                            "Higher-priced same-tag sibling co-occurs "
                            f"({count} checkouts, INV-14)."
                        ),
                        trigger_skus=[trigger],
                        suggested_sku=sibling,
                        source_signals={"trigger": "auto_upgrade_candidate",
                                        "together_count": count},
                        trace_id=trace_id,
                    )
                )
                break
    return drafts


def rule_why_stat(
    session: Session, merchant_id: str, rule: MerchandisingRule
) -> dict[str, Any]:
    """Why-stat for the merchant review panel (Q-045 access model gates the
    surface; this function only reads aggregates): co-occurrence of the
    trigger set with the suggestion, plus the suggestion's own 7d/30d units.
    Co-occurrence here is correlation for review context, not causal lift
    (explicitly remaining, stage spec)."""
    from openstore.core.campaigns import get_analytics_view

    pairs = co_occurrence_counts(session, merchant_id)
    together = 0
    for trigger in rule.trigger_skus:
        ordered = sorted([trigger, rule.suggested_sku])
        pair: tuple[str, str] = (ordered[0], ordered[1])
        together += pairs.get(pair, 0)
    units = {a["sku"]: a for a in get_analytics_view(session, merchant_id)}
    mine = units.get(rule.suggested_sku, {"units_sold_7d": 0, "units_sold_30d": 0})
    return {
        "together_count": together,
        "units_sold_7d": mine["units_sold_7d"],
        "units_sold_30d": mine["units_sold_30d"],
    }
