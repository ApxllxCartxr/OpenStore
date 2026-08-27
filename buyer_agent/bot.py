import asyncio
import uuid

import discord

from buyer_agent.discover import discover_merchant, discover_auth_server
from buyer_agent.oauth_client import (
    generate_pkce_pair,
    LoopbackServer,
    open_authorize_url,
    exchange_code_for_token,
    load_or_refresh_token,
    save_token,
)
from buyer_agent.graph import build_graph, ConversationState
from buyer_agent.intent import ensure_intent_policy_signed

MERCHANT_URL = "http://localhost:8000"
REDIRECT_URI = "http://127.0.0.1:8765/callback"
CLIENT_NAME = "openstore-buyer-agent"
SCOPES = ["catalog:read", "cart:write", "checkout:initiate", "checkout:confirm"]

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

compiled_graph = build_graph()
_signed_policy_cache: set[str] = set()


async def on_message_handler(message: discord.Message) -> None:
    """Handle messages in the #buyer-agent channel."""
    if message.author.bot:
        return
    if not message.content:
        return

    conversation_id = f"{message.channel.id}-{message.author.id}"

    token = await _get_or_create_token()

    if conversation_id not in _signed_policy_cache:
        await message.channel.send("Checking Intent Policy status...")
        already_signed = ensure_intent_policy_signed(
            MERCHANT_URL, user_id="default-user"
        )
        if already_signed:
            _signed_policy_cache.add(conversation_id)
            await message.channel.send(
                "Intent Policy is active. Shopping within your signed bounds."
            )
        else:
            await message.channel.send(
                "Please sign your Intent Policy in the browser that just opened. "
                "I'll continue once you've completed the signing ceremony."
            )
            return

    state: ConversationState = {
        "conversation_id": conversation_id,
        "buyer_id": str(message.author.id),
        "merchant_url": MERCHANT_URL,
        "mcp_endpoint": f"{MERCHANT_URL}/agent/mcp",
        "token": token,
        "last_user_message": message.content,
    }

    config = {"configurable": {"thread_id": conversation_id}}

    try:
        result = await asyncio.to_thread(compiled_graph.invoke, state, config=config)
        reply = result.get("reply_text", "I couldn't process that request.")
    except Exception as e:
        reply = f"Error processing request: {e}"

    await message.channel.send(reply)


async def _get_or_create_token() -> str:
    """Discover merchant, register OAuth client, get or refresh a token."""
    descriptor = discover_merchant(MERCHANT_URL)
    as_metadata = discover_auth_server(descriptor)

    token_endpoint = as_metadata["token_endpoint"]
    auth_endpoint = as_metadata["authorization_endpoint"]
    registration_endpoint = as_metadata["registration_endpoint"]

    cached = load_or_refresh_token(token_endpoint, CLIENT_NAME)
    if cached:
        return cached

    import httpx

    reg_resp = httpx.post(
        registration_endpoint,
        json={"client_name": CLIENT_NAME, "redirect_uris": [REDIRECT_URI]},
        timeout=10.0,
    )
    reg_resp.raise_for_status()
    client_id = reg_resp.json()["client_id"]

    code_verifier, code_challenge = generate_pkce_pair()
    state_token = uuid.uuid4().hex

    server = LoopbackServer()
    server.start()
    try:
        open_authorize_url(
            auth_endpoint, client_id, SCOPES, REDIRECT_URI, code_challenge, state_token
        )
        code, returned_state = server.wait_for_code(timeout=120.0)
    finally:
        server.stop()

    if code is None:
        raise RuntimeError("OAuth authorization timed out or was denied")

    if returned_state != state_token:
        raise RuntimeError("OAuth state mismatch — possible CSRF")

    token_data = exchange_code_for_token(
        token_endpoint, code, code_verifier, REDIRECT_URI, client_id
    )
    save_token(token_data, client_id)
    return token_data["access_token"]


def main():
    import os
    from dotenv import load_dotenv

    load_dotenv()

    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in environment")

    bot.event(on_message_handler)
    bot.run(token)


if __name__ == "__main__":
    main()
