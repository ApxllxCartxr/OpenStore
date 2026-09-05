# tests/stage12/_merchant_server.py
# Helper executed in a SUBPROCESS: runs one real merchant HTTP server for
# FederatingMCPClient/HttpMCPClient tests. A subprocess (not an in-process
# TestClient) is required because openstore.surfaces.catalog.CATALOG_CACHE is
# a module global — two merchants alive at once in one interpreter would
# clobber each other's catalog, exactly the cross-tenant leak S10.1 guards
# against for policies/campaigns. Mirrors tests/stage10/_merchant_run.py.
#
# Usage: python _merchant_server.py <db_path> <merchant_name> <catalog_path> <port>
# Prints one JSON line {"client_id": ..., "client_secret": ...} once the
# OAuth client is registered, then blocks serving HTTP until killed.

from __future__ import annotations

import json
import sys

import uvicorn
from openstore.config import (
    CampaignSettings,
    DatabaseConfig,
    DiscordConfig,
    LLMSettings,
    MerchantConfig,
    RazorpayConfig,
    Settings,
    WebAuthnConfig,
)
from openstore.core.database import init_database, session_scope
from openstore.core.oauth import register_client
from openstore.server import create_app


def main() -> None:
    db_path, merchant_name, catalog_path, port_s = sys.argv[1:5]
    port = int(port_s)

    cfg = Settings(
        merchant=MerchantConfig(name=merchant_name, currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token",
            buyer_trace_channel_id=1,
            merchant_trace_channel_id=2,
            money_trace_channel_id=3,
            alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OS", origin=f"http://127.0.0.1:{port}"),
        database=DatabaseConfig(url=f"sqlite:///{db_path}"),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )
    cfg.catalog_path = catalog_path
    init_database(cfg)

    with session_scope(cfg) as session:
        client_id, client_secret = register_client(
            session,
            client_name="federated-buyer",
            redirect_uris=[],
            grant_types=["client_credentials"],
            scopes=["catalog:read", "order:read"],
        )

    print(json.dumps({"client_id": client_id, "client_secret": client_secret}), flush=True)

    app = create_app(cfg)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


if __name__ == "__main__":
    main()
