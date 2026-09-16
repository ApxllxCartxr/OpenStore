# OpenStore first-run wizard — the interactive half of `openstore init`.
#
# Rich prompts rather than a Textual app: first-run setup is a linear form
# with no concurrent panes, and rich is already a declared dependency
# (textual reaches the venv only through mutmut, a dev-only dep).
#
# Two invariants the wizard exists to enforce, both of which the static
# template got wrong:
#   - Secrets never land in the YAML. Every credential is written to .env and
#     referenced as ${VAR} — the rule load_config already applies.
#   - A platform catalog source is proven before it is written, through the
#     same build_adapter()/health_check() pair that POST /merchant/setup uses.
#     A source that cannot connect is never silently persisted.
#
# WebAuthn rp_id/origin are DERIVED from the deployment answer. The static
# template hardcoded localhost:8000, which passes locally and then breaks
# passkeys on the merchant's real origin — the failure this wizard removes.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

BACK = ":back"

# Discord is structurally required by Settings (DiscordConfig has no
# defaults) but nothing on the `serve` path logs in — only the bot processes
# do. A merchant who says no therefore gets a disabled, zeroed block rather
# than the four invented channel IDs the old template wrote. Making the
# block itself optional is a Settings change, deliberately not done here.
_DISCORD_OFF: dict[str, Any] = {
    "bot_token": "",
    "buyer_trace_channel_id": 0,
    "merchant_trace_channel_id": 0,
    "money_trace_channel_id": 0,
    "alerts_channel_id": 0,
    "buyer_bot_enabled": False,
}

_HEADER = """\
# OpenStore merchant config — written by `openstore init`.
# Secrets live in .env and are referenced as ${VAR}; never inline them here.
# Re-run `openstore init` to regenerate, or edit in place.
"""

_DISCORD_OFF_NOTE = """\
# Discord is off: buyer_bot_enabled is false and the channel IDs are zeroed
# placeholders (the block is required by config.Settings). Fill them in and
# flip buyer_bot_enabled to true to run the buyer/merchant bots.
"""


class Back(Exception):
    """A prompt saw the :back sentinel — the runner steps one screen back."""


@dataclass(frozen=True)
class SourceField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    default: str | None = None


@dataclass(frozen=True)
class SourceSpec:
    kind: str
    label: str
    fields: tuple[SourceField, ...] = ()


# Order is the menu order: YAML first (no credentials), then the platforms
# most likely to be asking, then the file/spreadsheet fallbacks.
SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("yaml", "YAML file (edit by hand, or in /merchant/catalog)"),
    SourceSpec(
        "woocommerce",
        "WooCommerce",
        (
            SourceField("base_url", "Store URL (https://shop.example.com)"),
            SourceField("consumer_key", "Consumer key", secret=True),
            SourceField("consumer_secret", "Consumer secret", secret=True),
        ),
    ),
    SourceSpec(
        "shopify",
        "Shopify",
        (
            SourceField("store_domain", "Store domain (shop.myshopify.com)"),
            SourceField("client_id", "Client ID", secret=True),
            SourceField("client_secret", "Client secret", secret=True),
        ),
    ),
    SourceSpec(
        "bigcommerce",
        "BigCommerce",
        (
            SourceField("store_hash", "Store hash"),
            SourceField("access_token", "Access token", secret=True),
        ),
    ),
    SourceSpec(
        "magento",
        "Magento",
        (
            SourceField("base_url", "Base URL (https://shop.example.com)"),
            SourceField("access_token", "Access token", secret=True),
        ),
    ),
    SourceSpec(
        "wix",
        "Wix Stores",
        (SourceField("instance_token", "App instance token", secret=True),),
    ),
    SourceSpec(
        "zoho",
        "Zoho Commerce",
        (
            SourceField("dc", "Data centre suffix (in / com / eu)", default="in"),
            SourceField("client_id", "Client ID", secret=True),
            SourceField("client_secret", "Client secret", secret=True),
            SourceField("refresh_token", "Refresh token", secret=True),
            SourceField("organization_id", "Organization ID", required=False),
        ),
    ),
    SourceSpec(
        "csv",
        "CSV file or published CSV URL",
        (
            SourceField("url", "Published CSV URL", required=False),
            SourceField("path", "Local CSV path", required=False),
        ),
    ),
    SourceSpec(
        "sheets",
        "Google Sheets (published CSV URL)",
        (SourceField("url", "Published CSV URL"),),
    ),
)

SOURCES_BY_KIND: dict[str, SourceSpec] = {s.kind: s for s in SOURCES}


@dataclass
class WizardState:
    """Every answer, in config shape but with secrets still in the clear.

    `env` maps env var name -> real secret; `source` holds the catalog source
    with its real credentials (what the connection test needs). Rendering
    swaps the secrets for ${VAR} on the way into YAML.
    """

    merchant: str = ""
    currency: str = "INR"
    deployment: str = "same-origin"
    public_base_url: str | None = None
    source_kind: str = "yaml"
    source: dict[str, Any] = field(default_factory=dict)
    source_verified: bool = False
    discord_enabled: bool = False
    discord: dict[str, Any] = field(default_factory=dict)
    database_url: str = "sqlite:///openstore.db"
    env: dict[str, str] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.merchant.lower().replace(" ", "-").replace("'", "")


# --------------------------------------------------------------- prompting


def _ask(
    console: Console,
    label: str,
    *,
    default: str | None = None,
    secret: bool = False,
    required: bool = True,
) -> str:
    """One prompt. `:back` raises Back; empty is refused unless optional."""
    while True:
        answer = Prompt.ask(
            f"  [bold]{label}[/bold]",
            console=console,
            password=secret,
            default=default if default is not None else "",
            show_default=default is not None,
        ).strip()
        if answer == BACK:
            raise Back()
        if answer or not required:
            return answer
        console.print("  [red]Required.[/red]")


def _ask_int(console: Console, label: str, *, default: int | None = None) -> int:
    while True:
        raw = _ask(console, label, default=str(default) if default is not None else None)
        try:
            return int(raw)
        except ValueError:
            console.print(f"  [red]{raw!r} is not a number.[/red]")


def _ask_choice(console: Console, labels: list[str], *, default: int = 0) -> int:
    """Numbered menu; returns the chosen index."""
    for i, text in enumerate(labels, start=1):
        marker = "[cyan]>[/cyan]" if i - 1 == default else " "
        console.print(f"  {marker} {i}. {text}")
    console.print()
    while True:
        raw = _ask(console, "Choice", default=str(default + 1))
        try:
            index = int(raw) - 1
        except ValueError:
            console.print(f"  [red]{raw!r} is not a number.[/red]")
            continue
        if 0 <= index < len(labels):
            return index
        console.print(f"  [red]Pick 1-{len(labels)}.[/red]")


def _confirm(console: Console, label: str, *, default: bool = True) -> bool:
    return Confirm.ask(f"  [bold]{label}[/bold]", console=console, default=default)


def _header(console: Console, step: int, total: int, title: str) -> None:
    console.print()
    console.rule(f"[bold]OpenStore setup[/bold] — {step} of {total} · {title}", align="left")
    console.print()


# ------------------------------------------------------------------- steps


def step_merchant(console: Console, state: WizardState) -> None:
    state.merchant = _ask(console, "Merchant name", default=state.merchant or None)
    state.currency = _ask(console, "Currency (ISO 4217)", default=state.currency).upper()


def step_deployment(console: Console, state: WizardState) -> None:
    console.print("  How will buyers reach the sidecar?\n")
    choice = _ask_choice(
        console,
        [
            "Same origin as my site (reverse proxy at /openstore, say)",
            "Its own subdomain (https://openstore.myshop.example)",
            "Local development only (http://localhost:8000)",
        ],
        default=0 if state.deployment == "same-origin" else 1,
    )
    if choice == 2:
        state.deployment, state.public_base_url = "local", "http://localhost:8000"
        return
    state.deployment = "same-origin" if choice == 0 else "subdomain"
    console.print()
    console.print(
        "  [dim]Passkeys bind to this origin. It must be the HTTPS origin\n"
        "  merchants actually visit, or sign-in breaks in production.[/dim]\n"
    )
    while True:
        url = _ask(console, "Public URL", default=state.public_base_url)
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            state.public_base_url = f"{parsed.scheme}://{parsed.netloc}"
            return
        console.print("  [red]Need a full origin, e.g. https://shop.example.com[/red]")


def step_payments(console: Console, state: WizardState) -> None:
    console.print("  [dim]Razorpay is the only PSP driver today (INR).[/dim]\n")
    state.env["RAZORPAY_KEY_ID"] = _ask(
        console, "Razorpay key ID", default=state.env.get("RAZORPAY_KEY_ID")
    )
    state.env["RAZORPAY_KEY_SECRET"] = _ask(console, "Razorpay key secret", secret=True)
    webhook = _ask(console, "Razorpay webhook secret (blank to skip)", secret=True, required=False)
    if webhook:
        state.env["RAZORPAY_WEBHOOK_SECRET"] = webhook


def _env_var(kind: str, key: str) -> str:
    return f"{kind.upper()}_{key.upper()}"


def _test_source(console: Console, raw: dict[str, Any]) -> bool:
    """Connect-and-test, the same pair POST /merchant/setup runs."""
    from openstore.surfaces.adapters.errors import AdapterError
    from openstore.surfaces.adapters.registry import build_adapter

    console.print()
    with console.status("  Testing connection..."):
        try:
            adapter = build_adapter(raw)
            health = adapter.health_check()
            count = health.item_count
            if health.ok and count is None:
                count = len(adapter.fetch_items())
        except AdapterError as exc:
            console.print(f"  [red]✗ {exc.reason_code}[/red] {exc.message}")
            return False
        except (ValueError, OSError) as exc:
            console.print(f"  [red]✗ connection failed[/red] {exc}")
            return False
    if not health.ok:
        console.print(f"  [red]✗ {adapter.name}[/red] {health.detail or 'unhealthy'}")
        return False
    console.print(f"  [green]✓ {adapter.name}[/green], {count} items")
    return True


def step_catalog(console: Console, state: WizardState) -> None:
    console.print("  Where does the product catalog live?\n")
    kinds = [s.kind for s in SOURCES]
    index = _ask_choice(
        console,
        [s.label for s in SOURCES],
        default=kinds.index(state.source_kind) if state.source_kind in kinds else 0,
    )
    spec = SOURCES[index]
    state.source_kind = spec.kind
    if spec.kind == "yaml":
        # Legacy catalog_path default: writing no catalog_source block is what
        # makes load_config point at catalog.yaml next to the config file.
        state.source, state.source_verified = {}, True
        console.print("\n  [green]✓[/green] catalog.yaml will be created next to the config.")
        return

    console.print()
    while True:
        raw: dict[str, Any] = {"type": spec.kind}
        for f in spec.fields:
            value = _ask(
                console,
                f.label,
                default=f.default,
                secret=f.secret,
                required=f.required,
            )
            if value:
                raw[f.key] = value
        if spec.kind == "csv" and not (raw.get("url") or raw.get("path")):
            console.print("  [red]CSV needs a URL or a local path.[/red]\n")
            continue
        if _test_source(console, raw):
            state.source, state.source_verified = raw, True
            return
        console.print()
        if not _confirm(console, "Try again?", default=True):
            state.source, state.source_verified = raw, False
            console.print("  [yellow]Saving unverified — /merchant/setup can retest it.[/yellow]")
            return
        console.print()


def step_discord(console: Console, state: WizardState) -> None:
    console.print(
        "  [dim]Discord carries the buyer bot and the trace/alert channels.\n"
        "  Skip it — the storefront, agent surfaces and merchant console\n"
        "  all work without it.[/dim]\n"
    )
    state.discord_enabled = _confirm(console, "Use Discord?", default=state.discord_enabled)
    if not state.discord_enabled:
        state.discord = {}
        return
    console.print()
    state.env["DISCORD_BOT_TOKEN"] = _ask(console, "Bot token", secret=True)
    state.discord = {
        "buyer_trace_channel_id": _ask_int(console, "Buyer trace channel ID"),
        "merchant_trace_channel_id": _ask_int(console, "Merchant trace channel ID"),
        "money_trace_channel_id": _ask_int(console, "Money trace channel ID"),
        "alerts_channel_id": _ask_int(console, "Alerts channel ID"),
    }


def step_database(console: Console, state: WizardState) -> None:
    choice = _ask_choice(
        console,
        [
            "SQLite file (single machine, no setup)",
            "Postgres (production; DATABASE__URL overrides this at runtime)",
        ],
        default=0 if state.database_url.startswith("sqlite") else 1,
    )
    if choice == 0:
        state.database_url = "sqlite:///openstore.db"
        return
    console.print()
    state.database_url = _ask(
        console,
        "Postgres URL",
        default="postgresql+psycopg://openstore:secret@localhost:5432/openstore",
    )


def step_review(console: Console, state: WizardState) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Merchant", f"{state.merchant} ({state.currency})")
    table.add_row("Deployment", state.deployment)
    table.add_row("Public URL", state.public_base_url or "derived from request")
    table.add_row("Passkey origin", _webauthn(state)["origin"])
    table.add_row(
        "Catalog",
        state.source_kind + ("" if state.source_verified else "  [yellow](unverified)[/yellow]"),
    )
    table.add_row("Discord", "on" if state.discord_enabled else "off")
    table.add_row("Database", state.database_url)
    table.add_row("Secrets", f"{len(state.env)} → .env")
    console.print(Panel(table, title="Review", title_align="left", border_style="cyan"))
    console.print()
    if not _confirm(console, "Write these files?", default=True):
        raise Back()


STEPS: tuple[tuple[str, Any], ...] = (
    ("Merchant", step_merchant),
    ("Deployment", step_deployment),
    ("Payments", step_payments),
    ("Catalog source", step_catalog),
    ("Discord", step_discord),
    ("Database", step_database),
    ("Review", step_review),
)


def run_wizard(console: Console, state: WizardState) -> WizardState:
    console.print()
    console.print(
        Panel(
            "Answers are written to a config YAML, a .env and (for YAML\n"
            "catalogs) a catalog.yaml. Type [bold]:back[/bold] at any prompt\n"
            "to step back.",
            title="OpenStore first-run setup",
            title_align="left",
            border_style="cyan",
        )
    )
    index = 0
    while index < len(STEPS):
        title, step = STEPS[index]
        _header(console, index + 1, len(STEPS), title)
        try:
            step(console, state)
        except Back:
            index = max(0, index - 1)
            continue
        index += 1
    return state


# --------------------------------------------------------------- rendering


def _webauthn(state: WizardState) -> dict[str, str]:
    """rp_id/origin derived from the deployment answer, never hardcoded."""
    origin = state.public_base_url or "http://localhost:8000"
    host = urlparse(origin).hostname or "localhost"
    return {"rp_id": host, "rp_name": state.merchant, "origin": origin}


def config_from_state(state: WizardState) -> dict[str, Any]:
    """Config dict with every secret replaced by its ${VAR} reference."""
    cfg: dict[str, Any] = {
        "merchant": {"name": state.merchant, "currency": state.currency},
        "razorpay": {
            "key_id": "${RAZORPAY_KEY_ID}",
            "key_secret": "${RAZORPAY_KEY_SECRET}",
        },
        "discord": dict(_DISCORD_OFF),
        "webauthn": _webauthn(state),
        "database": {"url": state.database_url},
        "llm": {"model": "gpt-4o-mini", "temperature": 0.2},
        "campaign": {"min_bps": 500, "max_bps": 3000, "max_active": 5},
    }
    if "RAZORPAY_WEBHOOK_SECRET" in state.env:
        cfg["razorpay"]["webhook_secret"] = "${RAZORPAY_WEBHOOK_SECRET}"
    if state.discord_enabled:
        cfg["discord"] = {"bot_token": "${DISCORD_BOT_TOKEN}", **state.discord}
    if state.public_base_url and state.deployment != "local":
        cfg["public_base_url"] = state.public_base_url
    if state.source_kind != "yaml":
        spec = SOURCES_BY_KIND[state.source_kind]
        secrets = {f.key for f in spec.fields if f.secret}
        cfg["catalog_source"] = {
            key: f"${{{_env_var(state.source_kind, key)}}}" if key in secrets else value
            for key, value in state.source.items()
        }
    return cfg


def env_from_state(state: WizardState) -> dict[str, str]:
    """Every secret, including the catalog source's, keyed by env var."""
    env = dict(state.env)
    if state.source_kind != "yaml":
        spec = SOURCES_BY_KIND[state.source_kind]
        for f in spec.fields:
            if f.secret and f.key in state.source:
                env[_env_var(state.source_kind, f.key)] = str(state.source[f.key])
    return env


def render_env(env: dict[str, str]) -> str:
    lines = ["# OpenStore secrets — written by `openstore init`. Never commit this."]
    lines += [f"{name}={value}" for name, value in env.items()]
    lines.append("")
    lines.append("# Production database override (wins over the config YAML):")
    lines.append("# DATABASE__URL=postgresql+psycopg://openstore:secret@db:5432/openstore")
    return "\n".join(lines) + "\n"


def render_config(state: WizardState, cfg: dict[str, Any]) -> str:
    import yaml

    header = _HEADER if state.discord_enabled else _HEADER + _DISCORD_OFF_NOTE
    return header + "\n" + yaml.safe_dump(cfg, sort_keys=False)
