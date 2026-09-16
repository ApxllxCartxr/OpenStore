# OpenStore core — merchant-settings DB overlay (S24 DECISION-045, S25).
#
# Precedence is DB overlay > YAML > default. Originally this was applied
# only inside merchant.py's own request handlers (console display plus
# WebAuthn/campaign enforcement); Stage 25's catalog_source needs the SAME
# resolution reachable from the buyer-facing catalog path (webcart, the MCP
# catalog feed, campaigns, CLI) — not just the merchant console — or a
# saved platform connection (e.g. "connect WooCommerce") never actually
# serves anything, it only ever shows correctly on the Settings page.

from __future__ import annotations

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlmodel import Session, select

from openstore.config import Settings
from openstore.core.database import get_session
from openstore.models import MerchantSetting


def read_overlay(db: Session) -> dict[str, str]:
    return {row.key: row.value for row in db.exec(select(MerchantSetting)).all()}


def apply_overlay(config_base: Settings, overlay: dict[str, str]) -> Settings:
    """A deep copy of config_base with DB-stored settings applied. Never
    mutates the shared process config (concurrent requests must not race on
    it)."""
    eff = config_base.model_copy(deep=True)
    for key, value in overlay.items():
        if key == "merchant.name":
            eff.merchant.name = value
        elif key == "campaign.min_bps":
            eff.campaign.min_bps = int(value)
        elif key == "campaign.max_bps":
            eff.campaign.max_bps = int(value)
        elif key == "campaign.max_active":
            eff.campaign.max_active = int(value)
        elif key == "evidence_share_ttl_days":
            eff.evidence_share_ttl_days = int(value)
        elif key == "webauthn.rp_id":
            eff.webauthn.rp_id = value
        elif key == "webauthn.origin":
            eff.webauthn.origin = value
        elif key == "public_base_url":
            eff.public_base_url = value or None
        elif key == "catalog_source":
            import json

            from pydantic import TypeAdapter

            from openstore.config import CatalogSource

            eff.catalog_source = TypeAdapter(CatalogSource).validate_python(json.loads(value))
            # An overlay catalog_source is a strictly newer, explicit choice
            # superseding whatever legacy origin the YAML config carries —
            # otherwise a merchant with a working catalog.yaml (the common
            # case: `openstore init` writes one) who connects a platform via
            # Settings hits catalog.adapter_multiple_sources on every
            # request instead of the new source ever taking effect.
            eff.catalog_path = None
            eff.shopify = None
    return eff


def effective_settings(config: Settings, session: Session | None = None) -> Settings:
    """Effective config for call sites outside the merchant console
    (catalog serving, campaigns, CLI).

    A missing merchant_settings table (OperationalError/ProgrammingError,
    depending on the DB backend) means this config has never been served
    through the merchant console — not a corrupted database — so tools that
    run against a brand-new config (e.g. `openstore catalog-validate` before
    the first `openstore serve`) see the plain YAML config unchanged rather
    than erroring on a not-yet-migrated database. A process-global "schema
    ready" flag was considered and rejected: it leaks across the
    differently-scoped per-test databases this function must also work
    against.

    Callers that are already inside a transaction MUST pass their own
    session. The fallback below opens a second one, and under SQLite's
    StaticPool (the in-memory test/dev path) every Session shares a single
    connection — closing this one rolls back the caller's in-flight work.
    """
    if session is not None:
        return apply_overlay(config, read_overlay(session))
    db = get_session(config)
    try:
        overlay = read_overlay(db)
    except (OperationalError, ProgrammingError):
        return config
    finally:
        db.close()
    return apply_overlay(config, overlay)
