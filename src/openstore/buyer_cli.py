# OpenStore buyer CLI — S12 step 7 / step 8.
# The buyer agent's own process: no FastAPI app for money/PSP/signing (those
# live in merchant processes, R0.10) — but S12 step 8 adds one small internal
# FastAPI surface (surfaces/buyer_internal.py) alongside the Discord bot, so
# a merchant can ping this process once a buyer finishes signing a policy.
# That surface carries no authority of its own; see its module docstring.

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import cast

import typer
from rich.console import Console

from openstore.buyer_config import load_buyer_config
from openstore.config import Settings

app = typer.Typer(
    name="openstore-buyer",
    help="OpenStore federated buyer agent — shops across multiple merchant sidecars",
    no_args_is_help=True,
)
console = Console()


@app.command()
def serve(
    config_path: Path = typer.Argument(..., help="Path to buyer config YAML"),
    verify_origins: bool = typer.Option(
        True,
        "--verify-origins/--no-verify-origins",
        help="Fetch each merchant's manifest at startup and fail loud on a mismatch (R0.5)",
    ),
) -> None:
    """Start the buyer agent's Discord bot."""
    console.print(f"[blue]Loading buyer config from {config_path}...[/blue]")
    config = load_buyer_config(config_path, verify_origins=verify_origins)
    console.print(
        f"[green]✓[/green] Buyer config loaded — {len(config.merchants)} merchant(s): "
        + ", ".join(m.name for m in config.merchants)
    )

    # SID-3-style boot order: config → migrations → serve. Applies to the
    # buyer's OWN database (ShoppingSession rows), never a merchant's —
    # apply_migrations, not init_database, which is test-only (see its
    # docstring in core/database.py).
    console.print("[blue]Applying database migrations...[/blue]")
    from openstore.core.database import apply_migrations

    # BuyerAgent/DiscordNotifier/apply_migrations only ever touch the
    # attributes BuyerSettings and Settings share (config.discord,
    # config.database) — the merchant-only fields (config.merchant,
    # config.razorpay, ...) are never read on this dispatch path
    # (BuyerAgent._submit_cart routes federated carts to
    # _submit_federated_cart, which never calls merchant_id(self.config)).
    # This cast documents that instead of widening BuyerAgent's type for a
    # structural relationship that only holds for the federated path.
    settings_view = cast(Settings, config)
    apply_migrations(settings_view)
    console.print("[green]✓[/green] Migrations at head")

    import discord

    from openstore.agents.buyer_agent import BuyerAgent, FederatedBuyerBot
    from openstore.agents.mcp_client import FederatingMCPClient
    from openstore.notifier import set_discord_client

    mcp_client = FederatingMCPClient(config.merchants)
    agent = BuyerAgent(settings_view, mcp_client, resume_url=config.signing_complete_url())
    bot = FederatedBuyerBot(settings_view, agent)

    intents = discord.Intents.default()
    intents.message_content = True  # privileged; enable in the Discord dev portal
    client = discord.Client(intents=intents)
    set_discord_client(client)
    bot.register(client)

    import uvicorn
    from fastapi import FastAPI

    from openstore.surfaces.buyer_internal import create_buyer_internal_router
    from openstore.surfaces.buyer_studio import create_buyer_studio_router

    internal_app = FastAPI(title="openstore-buyer internal")
    internal_app.include_router(create_buyer_internal_router(config, agent))
    internal_app.include_router(create_buyer_studio_router(config, agent))
    uv_config = uvicorn.Config(
        internal_app, host="127.0.0.1", port=config.resume_port, log_config=None
    )
    uv_server = uvicorn.Server(uv_config)

    console.print(
        f"[blue]Starting internal signing-complete listener on "
        f"127.0.0.1:{config.resume_port}...[/blue]"
    )
    console.print("[blue]Starting buyer Discord bot...[/blue]")

    async def _run() -> None:
        # Mirrors server.py's lifespan shutdown handling: two concurrent
        # tasks, cancelled together on exit rather than one blocking call
        # (discord.Client.run) that leaves no room for the second server.
        discord_task = asyncio.create_task(client.start(config.discord.bot_token))
        server_task = asyncio.create_task(uv_server.serve())
        try:
            await asyncio.gather(discord_task, server_task)
        finally:
            uv_server.should_exit = True
            discord_task.cancel()
            for task in (discord_task, server_task):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await client.close()

    asyncio.run(_run())


if __name__ == "__main__":
    app()
