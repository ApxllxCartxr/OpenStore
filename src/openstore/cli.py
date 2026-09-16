# OpenStore CLI — init and serve commands

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console

from openstore.config import load_config

app = typer.Typer(
    name="openstore",
    help="OpenStore sidecar — make any merchant agent-transactable",
    no_args_is_help=True,
)
console = Console()


def build_config_dict(
    merchant: str,
    currency: str,
    public_base_url: str | None = None,
) -> dict[str, Any]:
    """Build config dict programmatically to avoid fragile string replacement."""
    cfg: dict[str, Any] = {
        "merchant": {"name": merchant, "currency": currency},
        "razorpay": {
            "key_id": "${RAZORPAY_KEY_ID}",
            "key_secret": "${RAZORPAY_KEY_SECRET}",
            "webhook_secret": "${RAZORPAY_WEBHOOK_SECRET}",
        },
        "discord": {
            "bot_token": "${DISCORD_BOT_TOKEN}",
            "buyer_trace_channel_id": 123456789012345678,
            "merchant_trace_channel_id": 123456789012345679,
            "money_trace_channel_id": 123456789012345680,
            "alerts_channel_id": 123456789012345681,
        },
        "webauthn": {
            "rp_id": "localhost",
            "rp_name": merchant,
            "origin": "http://localhost:8000",
        },
        # Production: set DATABASE__URL (nested-delimiter override) to a
        # postgresql+psycopg:// URL. The YAML default stays SQLite for local dev.
        "database": {"url": "sqlite:///openstore.db"},
        "llm": {"model": "gpt-4o-mini", "temperature": 0.2},
        "campaign": {"min_bps": 500, "max_bps": 3000, "max_active": 5},
    }
    # SID-1: subdomain deployment sets an explicit public_base_url; same-origin
    # reverse proxy leaves it unset (manifests derive origin from the request).
    if public_base_url:
        cfg["public_base_url"] = public_base_url
    return cfg


ENV_TEMPLATE = """# OpenStore environment variables
# Copy to .env and fill in

RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=whsec_xxxxxxxxxxxxxxxx

DISCORD_BOT_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Production database (Postgres). Local dev omits this and uses SQLite.
# DATABASE__URL=postgresql+psycopg://openstore:secret@db:5432/openstore
"""

CATALOG_TEMPLATE = """# OpenStore catalog — SKUs in integer paise (minor units)
# Tags are free-form strings used by IntentPolicy allowed_tags
# Add rows below, or manage the catalog at /merchant/catalog once serving.

  # - sku: "example_sku_1"
  #   name: "Example item 1"
  #   unit_minor: 145000
  #   tags: ["example"]
  #   related_skus: []
  #   description: ""

  # - sku: "example_sku_2"
  #   name: "Example item 2"
  #   unit_minor: 45000
  #   tags: ["example"]
  #   related_skus: ["example_sku_1"]
  #   description: ""

  # - sku: "example_sku_3"
  #   name: "Example item 3"
  #   unit_minor: 1450
  #   tags: ["example"]
  #   related_skus: []
  #   description: ""
"""


@app.command()
def init(
    merchant: str = typer.Option(..., "--merchant", "-m", help="Merchant name"),
    currency: str = typer.Option("INR", "--currency", "-c", help="Currency (ISO 4217)"),
    output_dir: Path = typer.Option(Path("."), "--output", "-o", help="Output directory"),
    deployment: str = typer.Option(
        "same-origin",
        "--deployment",
        "-d",
        help="SID-1 deployment mode: same-origin (reverse proxy) or subdomain",
    ),
    public_base_url: str | None = typer.Option(
        None,
        "--public-base-url",
        help="SID-1 public origin for subdomain deployments (e.g. https://openstore.gelateria.example)",
    ),
) -> None:
    """Initialize a new OpenStore sidecar project."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = merchant.lower().replace(" ", "-").replace("'", "")
    config_path = output_dir / f"{slug}.yaml"
    env_path = output_dir / ".env.example"
    catalog_path = output_dir / "catalog.yaml"

    # SID-1: subdomain mode requires an explicit public_base_url; same-origin
    # derives origin from the request at runtime.
    pub = public_base_url
    if deployment == "subdomain" and not pub:
        console.print("[red]✗[/red] --deployment subdomain requires --public-base-url")
        raise typer.Exit(code=2)

    # Write config template with merchant name
    config_dict = build_config_dict(merchant, currency, public_base_url=pub)
    config_path.write_text(yaml.safe_dump(config_dict, sort_keys=False))

    env_path.write_text(ENV_TEMPLATE)
    catalog_path.write_text(CATALOG_TEMPLATE)

    console.print(f"[green]✓[/green] Created {config_path}")
    console.print(f"[green]✓[/green] Created {env_path}")
    console.print(f"[green]✓[/green] Created {catalog_path}")
    console.print(f"[green]✓[/green] Deployment mode: {deployment}")
    console.print()
    console.print("Next steps:")
    console.print(f"  1. Copy {env_path} to .env and fill in your keys")
    console.print(f"  2. Edit {config_path} and {catalog_path} as needed")
    console.print(f"  3. Run: openstore serve {config_path}")


@app.command()
def serve(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
    host: str = typer.Option("0.0.0.0", "--host", "-h"),
    port: int = typer.Option(8000, "--port", "-p"),
) -> None:
    """Start the OpenStore execution server."""
    console.print(f"[blue]Loading config from {config_path}...[/blue]")
    config = load_config(config_path)
    console.print(f"[green]✓[/green] Config loaded for merchant: {config.merchant.name}")

    # SID-3 boot order: config → migrations → keys → workers → serve.
    console.print("[blue]Applying database migrations...[/blue]")
    from openstore.core.database import apply_migrations

    apply_migrations(config)
    console.print("[green]✓[/green] Migrations at head")

    # Import here to avoid circular imports
    import uvicorn

    from openstore.server import create_app

    fastapi_app = create_app(config)
    console.print(f"[blue]Starting server on {host}:{port}...[/blue]")
    uvicorn.run(fastapi_app, host=host, port=port, log_config=None)


@app.command("federation-register-buyer")
def federation_register_buyer(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
    buyer_name: str = typer.Option(
        "buyer-agent", "--buyer-name", help="OAuth client_name for the buyer agent"
    ),
) -> None:
    """Provision an OAuth client_credentials client for an out-of-process buyer agent."""
    config = load_config(config_path)

    from openstore.core.database import apply_migrations, session_scope
    from openstore.core.oauth import register_client

    apply_migrations(config)

    with session_scope(config) as session:
        client_id, client_secret = register_client(
            session,
            client_name=buyer_name,
            redirect_uris=[],
            grant_types=["client_credentials"],
            scopes=["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"],
        )

    console.print(f"[green]✓[/green] Registered buyer OAuth client: {buyer_name}")
    console.print(f"  client_id:     {client_id}")
    console.print(f"  client_secret: {client_secret}")
    console.print(
        "[yellow]Paste these into the buyer agent's config — the secret is shown once.[/yellow]"
    )


# --------------------------------------------------------------------- campaign
# DECISION-025: the Campaign Orchestrator (PRD Part 9) had no trigger anywhere in
# src/ — draft_campaign() and create_campaign() had zero callers, so the pipeline
# could never run. Drafting deliberately gets a CLI command rather than a route:
# PRD §9.2 makes the merchant a reviewer, not an author.
campaign_app = typer.Typer(help="Campaign / Offer Orchestrator (PRD Part 9)", no_args_is_help=True)
app.add_typer(campaign_app, name="campaign")


def _campaign_env(config_path: Path) -> Any:
    from openstore.core.database import apply_migrations

    config = load_config(config_path)
    apply_migrations(config)
    return config


@campaign_app.command("draft")
def campaign_draft(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
    calendar_event: str = typer.Option(
        "", "--calendar-event", help="Upcoming occasion to reason about, e.g. 'Diwali'"
    ),
    days: int = typer.Option(7, "--days", help="Length of the offer window in days"),
) -> None:
    """Draft a campaign from the aggregated analytics view and park it for approval.

    Ingest → LLM draft → deterministic validate → persist DRAFT → PENDING_APPROVAL.
    The LLM never publishes: the draft lands in Campaign Studio for a passkey approval.
    """
    import secrets
    from datetime import UTC, datetime, timedelta

    from openstore.agents.campaign_agent import CampaignAgent
    from openstore.config import merchant_id as _mid
    from openstore.core.campaigns import create_campaign, submit_for_approval
    from openstore.core.database import session_scope

    config = _campaign_env(config_path)
    mid = _mid(config)
    trace_id = f"campaign-draft-{secrets.token_hex(4)}"

    with session_scope(config) as session:
        draft = CampaignAgent(config).draft_campaign(session, mid, calendar_event or None, trace_id)
        starts_at = datetime.now(UTC).replace(tzinfo=None)
        campaign = create_campaign(
            session,
            config,
            merchant_id=mid,
            title=draft["title"],
            rationale=draft["rationale"],
            discount_bps=draft["discount_bps"],
            applies_to_skus=draft["applies_to_skus"],
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=days),
            source_signals=draft["source_signals"],
            trace_id=trace_id,
        )
        submit_for_approval(session, campaign.id, trace_id)
        campaign_id, title, bps, skus = (
            campaign.id,
            campaign.title,
            campaign.discount_bps,
            list(campaign.applies_to_skus),
        )

    console.print(f"[green]✓[/green] Drafted {campaign_id}: {title}")
    console.print(f"  discount: {bps / 100}%  skus: {', '.join(skus)}")
    console.print("  state:    PENDING_APPROVAL")
    console.print("[yellow]Approve it with your passkey at /campaign/studio.[/yellow]")


@campaign_app.command("list")
def campaign_list(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
    state: str = typer.Option("", "--state", help="Filter by campaign state"),
) -> None:
    """List campaigns and their states."""
    from sqlmodel import select

    from openstore.core.database import session_scope
    from openstore.models import Campaign, CampaignState

    config = _campaign_env(config_path)

    if state and state not in CampaignState.__members__:
        # R0.3: an unknown enum value is a hard error, never a silent empty result.
        raise typer.BadParameter(
            f"unknown campaign state {state!r}; expected one of "
            f"{', '.join(CampaignState.__members__)}"
        )

    with session_scope(config) as session:
        query = select(Campaign)
        if state:
            query = query.where(Campaign.state == CampaignState[state])
        rows = [
            (c.id, c.state.value, c.title, c.discount_bps, c.ends_at)
            for c in session.exec(query).all()
        ]

    if not rows:
        console.print("[yellow]No campaigns.[/yellow]")
        return
    for cid, cstate, title, bps, ends_at in rows:
        console.print(
            f"{cid}  [bold]{cstate:<16}[/bold] {bps / 100:>5}%  {title}  (ends {ends_at})"
        )


@campaign_app.command("pause")
def campaign_pause(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
    campaign_id: str = typer.Argument(..., help="Campaign to pause"),
) -> None:
    """Pause an ACTIVE campaign, dropping it out of the offer feed."""
    from openstore.core.campaigns import CampaignValidationError, pause_campaign
    from openstore.core.database import session_scope

    config = _campaign_env(config_path)
    try:
        with session_scope(config) as session:
            pause_campaign(session, campaign_id, f"campaign:{campaign_id}")
    except CampaignValidationError as e:
        console.print(f"[red]✗[/red] {e.reason_code}: {e.message}")
        raise typer.Exit(code=1) from e
    console.print(f"[green]✓[/green] Paused {campaign_id}")


@campaign_app.command("expire")
def campaign_expire(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
) -> None:
    """Run the PRD §9.4 expiry sweep once (the server runs it every 60s)."""
    from openstore.core.campaigns import expire_campaigns_due
    from openstore.core.database import session_scope

    config = _campaign_env(config_path)
    with session_scope(config) as session:
        expired = [c.id for c in expire_campaigns_due(session)]

    if not expired:
        console.print("[yellow]Nothing due to expire.[/yellow]")
        return
    console.print(f"[green]✓[/green] Expired {len(expired)}: {', '.join(expired)}")


@campaign_app.command("check-growth")
def campaign_check_growth(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
) -> None:
    """DECISION-034: run the autonomous growth-trigger check once on demand
    (the server runs it every campaign.growth_check_interval_seconds, default
    3600s — this is the same check, for testing/demo without waiting)."""
    from openstore.agents.campaign_agent import auto_draft_campaign_if_stalled
    from openstore.config import merchant_id as _mid
    from openstore.core.database import session_scope

    config = _campaign_env(config_path)
    mid = _mid(config)
    with session_scope(config) as session:
        campaign = auto_draft_campaign_if_stalled(session, config, mid)
        if campaign is None:
            result = None
        else:
            result = (
                campaign.id,
                campaign.title,
                campaign.discount_bps,
                list(campaign.applies_to_skus),
                list(campaign.source_signals.get("stalled_skus", [])),
            )

    if result is None:
        console.print("[yellow]Nothing stalled — no campaign drafted.[/yellow]")
        return
    campaign_id, title, bps, skus, stalled = result
    console.print(f"[green]✓[/green] Auto-drafted {campaign_id}: {title}")
    console.print(f"  discount: {bps / 100}%  skus: {', '.join(skus)}")
    console.print(f"  stalled:  {', '.join(stalled)}")
    console.print("  state:    PENDING_APPROVAL")
    console.print("[yellow]Approve it with your passkey at /campaign/studio.[/yellow]")


# ----------------------------------------------------------------------- orders
@app.command("orders")
def orders(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List recent checkouts with their state and evidence availability."""
    from sqlmodel import select

    from openstore.core.database import session_scope
    from openstore.models import Checkout

    config = _campaign_env(config_path)
    with session_scope(config) as session:
        rows = [
            (c.id, str(getattr(c.state, "value", c.state)), c.amount_minor, bool(c.poai_bundle))
            for c in session.exec(
                select(Checkout).order_by(Checkout.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
            ).all()
        ]

    if not rows:
        console.print("[yellow]No orders.[/yellow]")
        return
    for cid, cstate, amount, has_evidence in rows:
        mark = "evidence" if has_evidence else "—"
        console.print(f"{cid}  [bold]{cstate:<12}[/bold] ₹{amount / 100:>10.2f}  {mark}")


@app.command("catalog-validate")
def catalog_validate(
    config_path: Path = typer.Argument(..., help="Path to merchant config YAML"),
) -> None:
    """Load the catalog, compute its digest, and build its attestation.

    Catches a malformed or missing catalog before the feed serves it, rather than
    at the first agent request.
    """
    from openstore.config import merchant_id as _mid
    from openstore.surfaces.catalog import compute_catalog_digest, load_catalog

    config = load_config(config_path)
    items = load_catalog(config)
    digest = compute_catalog_digest(config)
    console.print(f"[green]✓[/green] {len(items)} item(s) for {_mid(config)}")
    console.print(f"  catalog_path: {config.catalog_path}")
    console.print(f"  digest:       {digest}")


@app.command("merchant-bot")
def merchant_bot_cmd(
    configs: list[Path] = typer.Argument(..., help="One or more merchant config YAML paths"),
) -> None:
    """Start the conversational, read-only MerchantBot (S14/DECISION-028).

    Runs as its OWN process with its OWN Discord identity (env var
    MERCHANT_BOT_TOKEN — distinct from any merchant's own bot_token, same
    pattern as BUYER_DISCORD_BOT_TOKEN for the buyer process), reading
    directly from each merchant's own database. Pass one config for a
    single-store bot, or several for one bot that reports across all of
    them.
    """
    import os

    from dotenv import load_dotenv

    # Settings.from_yaml() normally triggers this as a side effect of loading
    # a merchant config — but the token check here runs BEFORE any config is
    # loaded (fail loud on a missing token before doing anything else), so
    # .env must be loaded explicitly or MERCHANT_BOT_TOKEN is invisible even
    # when it's genuinely set there.
    load_dotenv()

    token = os.environ.get("MERCHANT_BOT_TOKEN")
    if not token:
        console.print("[red]MERCHANT_BOT_TOKEN is not set — nothing to log in with.[/red]")
        raise typer.Exit(1)

    settings_by_name = {}
    for config_path in configs:
        cfg = load_config(config_path)
        settings_by_name[cfg.merchant.name] = cfg
        console.print(f"[green]✓[/green] Loaded {cfg.merchant.name} ({config_path})")

    import asyncio

    import discord

    from openstore.agents.merchant_bot import MerchantBot

    bot = MerchantBot(settings_by_name)
    intents = discord.Intents.default()
    intents.message_content = True  # privileged; enable in the Discord dev portal
    client = discord.Client(intents=intents)
    bot.register(client)

    console.print("[blue]Starting merchant bot Discord client...[/blue]")
    asyncio.run(client.start(token))


if __name__ == "__main__":
    app()
