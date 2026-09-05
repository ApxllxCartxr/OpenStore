# OpenStore agents — MerchantBot (S14, DECISION-028): a conversational,
# read-only reporting bot for the merchant. Runs as its own process
# (`openstore merchant-bot <config...>`), its own Discord identity, reading
# directly from one or more merchants' own databases — no MCP, no OAuth,
# because this is the merchant's own trusted tool, not an arm's-length
# buyer.
#
# R0.10 by omission, not by prompt: every action in the closed set below is
# either read-only, or — suggest_campaign — produces only a PENDING_APPROVAL
# draft, the exact same non-authoritative artifact `openstore campaign draft`
# already produces from the CLI. There is no action that ever ACTIVATES a
# campaign, moves money, or touches an order: that still requires the real
# WebAuthn ceremony at /campaign/studio, which no chat surface can perform
# (R0.10: no chat surface holds or exercises signing authority). Nothing
# here can approve/reject/pause a campaign or touch money no matter what it
# proposes — the same safety argument cancel_order's server-side ownership
# check makes, applied here by simply never building the mutating path at
# all.

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from openstore.agents.llm import llm_chat
from openstore.config import Settings
from openstore.config import merchant_id as _merchant_id_of
from openstore.core.campaigns import compute_campaign_outcome, compute_merchant_headroom
from openstore.models import Campaign, Checkout

logger = logging.getLogger("openstore.merchant_bot")

_ACTIONS = frozenset(
    {"campaign_status", "exposure", "recent_orders", "suggest_campaign", "unknown"}
)

_SYSTEM_PROMPT = (
    "You are a reporting assistant for a merchant operating an OpenStore "
    "storefront. You can read data and report it back, and you can draft a "
    "NEW campaign proposal — but you can never approve, activate, reject, "
    "pause, or otherwise change anything that already exists. Respond with "
    "JSON only, matching exactly one of these five shapes:\n"
    '1. {"action": "campaign_status", "merchant": "<store name, optional>", '
    '"campaign": "<campaign title or keyword, optional>"} — the merchant '
    "asked about a campaign's status, discount, window, or what needs "
    "approval.\n"
    '2. {"action": "exposure", "merchant": "<store name, optional>"} — the '
    "merchant asked about spend headroom, exposure, or risk.\n"
    '3. {"action": "recent_orders", "merchant": "<store name, optional>", '
    '"limit": <int, optional>} — the merchant asked about recent orders '
    "or sales.\n"
    '4. {"action": "suggest_campaign", "merchant": "<store name, optional '
    'ONLY if this bot covers just one store>", "calendar_event": "<an '
    "occasion the merchant named, optional, e.g. 'Diwali' or 'weekend'>\"} "
    "— the merchant asked you to suggest, draft, or orchestrate a campaign "
    "or offer. This drafts a REAL proposal from real sales data and parks "
    "it for a human passkey approval — it never goes live on its own, and "
    "you do not choose the discount or SKUs yourself.\n"
    '5. {"action": "unknown", "message": "<a short, honest note that you '
    "can only report on campaigns, orders, exposure, and draft new campaign "
    'proposals>"} — anything else, including any request to approve, '
    "activate, or otherwise change something that already exists (that is "
    "never something you can do — say so plainly, don't pretend to do it)."
)


class MerchantBotError(Exception):
    """The LLM's response broke the closed action-set contract."""


def _parse_action(response: str) -> dict[str, Any]:
    try:
        draft = json.loads(response)
    except json.JSONDecodeError as e:
        raise MerchantBotError(f"invalid_json_response: {e}") from e
    if not isinstance(draft, dict) or draft.get("action") not in _ACTIONS:
        action = draft.get("action") if isinstance(draft, dict) else draft
        raise MerchantBotError(f"invalid_action: {action!r}")
    return draft


def _build_engine(config: Settings) -> Engine:
    """Mirrors core/database.get_engine's SQLite setup (StaticPool, WAL,
    busy_timeout) without touching its process-global cache — see
    MerchantBot.__init__."""
    from sqlalchemy import text

    connect_args = {"check_same_thread": False}
    if config.database.url.startswith("sqlite"):
        engine = create_engine(
            config.database.url, connect_args=connect_args, poolclass=StaticPool, echo=False
        )
    else:
        engine = create_engine(config.database.url, echo=False)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.commit()
    return engine


def _matching_merchants(
    configs: dict[str, Settings], filter_text: str | None
) -> list[tuple[str, Settings]]:
    items = list(configs.items())
    if not filter_text:
        return items
    needle = filter_text.strip().lower()
    return [(name, cfg) for name, cfg in items if needle in name.lower()]


def _report_campaign_status(
    name: str, session: Session, merchant_id: str, campaign_filter: str | None
) -> str:
    campaigns = list(
        session.exec(select(Campaign).where(Campaign.merchant_id == merchant_id)).all()
    )
    if campaign_filter:
        needle = campaign_filter.strip().lower()
        campaigns = [c for c in campaigns if needle in c.title.lower()]
    if not campaigns:
        return f"{name}: no campaigns found."
    lines = [f"{name}:"]
    for c in sorted(campaigns, key=lambda c: c.created_at or datetime.min, reverse=True):
        line = f"- {c.title} — {c.state.value} — {c.discount_bps / 100:.1f}% — ends {c.ends_at}"
        if c.source_signals.get("trigger") == "auto_stall_detected":
            line += (
                " — auto-detected (stalled: "
                + ", ".join(c.source_signals.get("stalled_skus", []))
                + ")"
            )
        if c.state.value == "PENDING_APPROVAL":
            line += " — needs your approval at /campaign/studio"
        elif c.state.value in ("ACTIVE", "PAUSED", "EXPIRED"):
            outcome = compute_campaign_outcome(session, c)
            delta = outcome["delta_pct"]
            delta_text = "n/a (no prior sales)" if delta is None else f"{delta:+.0f}%"
            line += (
                f" — units before/after: {outcome['units_before']}/{outcome['units_after']} "
                f"({delta_text})"
            )
        lines.append(line)
    return "\n".join(lines)


def _report_exposure(name: str, session: Session, merchant_id: str) -> str:
    headroom_minor = compute_merchant_headroom(session, merchant_id)
    return (
        f"{name}: ₹{headroom_minor / 100:.2f} of spend headroom remaining across active policies."
    )


def _report_recent_orders(name: str, session: Session, merchant_id: str, limit: int) -> str:
    checkouts = list(
        session.exec(
            select(Checkout)
            .where(Checkout.merchant_id == merchant_id)
            .order_by(Checkout.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
    )
    if not checkouts:
        return f"{name}: no orders yet."
    lines = [f"{name}:"]
    for c in checkouts:
        lines.append(f"- {c.id}  {c.state.value:<10}  ₹{c.amount_minor / 100:.2f}")
    return "\n".join(lines)


def _suggest_campaign(
    name: str,
    session: Session,
    config: Settings,
    merchant_id: str,
    calendar_event: str | None,
) -> str:
    """suggest_campaign action: the exact same ingest → LLM draft →
    deterministic validate → persist DRAFT → PENDING_APPROVAL pipeline
    `openstore campaign draft` already runs from the CLI (core/campaigns.py
    validates before anything reaches the DB — R0.9). Never activates
    anything: PENDING_APPROVAL still requires a real passkey at
    /campaign/studio, which this chat surface cannot perform (R0.10)."""
    import secrets
    from datetime import UTC, datetime, timedelta

    from openstore.agents.campaign_agent import CampaignAgent, CampaignDraftError
    from openstore.agents.llm import LLMError
    from openstore.core.campaigns import (
        CampaignValidationError,
        create_campaign,
        submit_for_approval,
    )

    trace_id = f"merchant-bot-suggest-{secrets.token_hex(4)}"
    try:
        draft = CampaignAgent(config).draft_campaign(session, merchant_id, calendar_event, trace_id)
        starts_at = datetime.now(UTC).replace(tzinfo=None)
        campaign = create_campaign(
            session,
            config,
            merchant_id=merchant_id,
            title=draft["title"],
            rationale=draft["rationale"],
            discount_bps=draft["discount_bps"],
            applies_to_skus=draft["applies_to_skus"],
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=7),
            source_signals=draft["source_signals"],
            trace_id=trace_id,
        )
        submit_for_approval(session, campaign.id, trace_id)
        session.commit()
    except (CampaignDraftError, CampaignValidationError, LLMError) as e:
        session.rollback()
        logger.warning("merchant bot: suggest_campaign failed for %s: %s", name, e)
        return f"{name}: couldn't draft a campaign right now ({e})."

    bps = campaign.discount_bps / 100
    skus = ", ".join(campaign.applies_to_skus) or "no SKUs matched"
    return (
        f"{name}: drafted {campaign.title} — {bps:.1f}% off {skus}. "
        "It's parked as PENDING_APPROVAL — approve it with your passkey at /campaign/studio."
    )


class MerchantBot:
    """Holds every merchant this bot instance can report on, keyed by
    display name (config's `merchant.name`) — one bot process, any number
    of merchants (one for a single-store demo, several for a general
    merchant bot). No session-parking: every message is a fresh, one-shot
    classify-then-report, unlike BuyerBot's multi-turn shopping loop —
    there's no iterative back-and-forth a reporting lookup needs."""

    def __init__(self, configs: dict[str, Settings]):
        if not configs:
            raise ValueError("MerchantBot needs at least one merchant config")
        self.configs = configs
        # core/database.py's get_engine/session_scope cache ONE engine for
        # the whole process (every other component here is one-merchant-
        # per-process, so that's the right shape for them) — MerchantBot is
        # the one component that genuinely needs several merchants' DBs
        # open at once, so it keeps its own engines instead of fighting
        # that shared global (which would otherwise silently point every
        # merchant but the first at the wrong database).
        self._engines: dict[str, Engine] = {
            name: _build_engine(cfg) for name, cfg in configs.items()
        }

    def _session_for(self, name: str) -> Session:
        return Session(self._engines[name])

    async def handle(self, text: str) -> str:
        response = llm_chat(
            next(iter(self.configs.values())),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        try:
            draft = _parse_action(response)
        except MerchantBotError as e:
            logger.warning("merchant bot: %s", e)
            return "Sorry, I didn't follow that — try asking about a campaign, exposure, or recent orders."

        action = draft["action"]
        if action == "unknown":
            message = draft.get("message")
            return (
                message
                if isinstance(message, str) and message
                else ("I can only report on campaigns, orders, and exposure.")
            )

        merchants = _matching_merchants(self.configs, draft.get("merchant"))
        if not merchants:
            known = ", ".join(self.configs)
            return f"I don't recognize that store — I know: {known}."

        if action == "suggest_campaign":
            if len(merchants) > 1:
                names = ", ".join(name for name, _ in merchants)
                return f"Which store do you mean — {names}?"
            name, cfg = merchants[0]
            session = self._session_for(name)
            try:
                return _suggest_campaign(
                    name, session, cfg, _merchant_id_of(cfg), draft.get("calendar_event")
                )
            finally:
                session.close()

        lines: list[str] = []
        for name, cfg in merchants:
            session = self._session_for(name)
            try:
                merchant_id = _merchant_id_of(cfg)
                if action == "campaign_status":
                    lines.append(
                        _report_campaign_status(name, session, merchant_id, draft.get("campaign"))
                    )
                elif action == "exposure":
                    lines.append(_report_exposure(name, session, merchant_id))
                else:  # action == "recent_orders"
                    limit = draft.get("limit", 5)
                    if not isinstance(limit, int) or limit < 1:
                        limit = 5
                    lines.append(_report_recent_orders(name, session, merchant_id, limit))
            finally:
                session.close()
        return "\n".join(lines)

    def register(self, client: Any) -> None:
        """Attaches a DM-only on_message handler — no bang command, no
        access restriction (explicit user call: matches BuyerBot's existing
        lack of gating)."""

        @client.event  # type: ignore[misc]
        async def on_message(message: Any) -> None:
            if message.author == client.user:
                return
            if message.guild is not None:
                return  # DM only
            content = (message.content or "").strip()
            if not content:
                return
            reply = await self.handle(content)
            await message.channel.send(reply)
