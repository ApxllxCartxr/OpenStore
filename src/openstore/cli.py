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
            "rp_name": "OpenStore Demo",
            "origin": "http://localhost:8000",
        },
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
"""

CATALOG_TEMPLATE = """# OpenStore catalog — SKUs in integer paise (minor units)
# Tags are free-form strings used by IntentPolicy allowed_tags

- sku: "gelato_vanilla"
  name: "Vanilla Gelato"
  price_minor: 15000
  tags: ["vegan", "gelato"]
  stock: 100

- sku: "gelato_chocolate"
  name: "Chocolate Gelato"
  price_minor: 15000
  tags: ["gelato"]
  stock: 100

- sku: "gelato_pistachio"
  name: "Pistachio Gelato"
  price_minor: 18000
  tags: ["gelato"]
  stock: 50

- sku: "cone_waffle"
  name: "Waffle Cone"
  price_minor: 3000
  tags: ["cone"]
  stock: 200

- sku: "topping_sprinkles"
  name: "Rainbow Sprinkles"
  price_minor: 2000
  tags: ["topping"]
  stock: 500
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

    config_path = output_dir / "gelateria.yaml"
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


if __name__ == "__main__":
    app()
