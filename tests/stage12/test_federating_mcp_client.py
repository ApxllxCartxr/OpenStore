# tests/stage12/test_federating_mcp_client.py
# S12 — HttpMCPClient (one merchant over HTTP) and FederatingMCPClient (fans
# search out across many). Two real merchant servers run in SUBPROCESSES
# (see _merchant_server.py for why: CATALOG_CACHE is a module global, so two
# merchants can't safely share one interpreter here).

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from openstore.agents.mcp_client import FederatingMCPClient, HttpMCPClient
from openstore.buyer_config import MerchantOrigin

ROOT = Path(__file__).resolve().parents[2]
_SERVER_SCRIPT = Path(__file__).parent / "_merchant_server.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"merchant server on port {port} never came up")


class _MerchantServer:
    """One merchant server running in a subprocess, torn down at fixture exit."""

    def __init__(self, name: str, catalog_items: list[dict], tmp_path: Path) -> None:
        self.name = name
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"

        db_path = tmp_path / f"{name.lower().replace(' ', '_')}.db"
        catalog_path = tmp_path / f"{name.lower().replace(' ', '_')}_catalog.yaml"
        catalog_path.write_text(yaml.safe_dump(catalog_items))

        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERVER_SCRIPT),
                str(db_path),
                name,
                str(catalog_path),
                str(self.port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=ROOT,
        )
        # First stdout line is the {"client_id", "client_secret"} JSON printed
        # right after OAuth client registration, before uvicorn.run blocks.
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read()
            raise RuntimeError(f"merchant server {name!r} exited before printing creds: {stderr}")
        creds = json.loads(line)
        self.client_id = creds["client_id"]
        self.client_secret = creds["client_secret"]
        _wait_until_up(self.port)

    def origin(self) -> MerchantOrigin:
        return MerchantOrigin(
            name=self.name,
            base_url=self.base_url,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


GELATO_ITEMS = [
    {"sku": "gelato_vanilla", "name": "Vanilla Gelato", "price_minor": 15000, "tags": ["gelato"]},
    {
        "sku": "gelato_pistachio",
        "name": "Pistachio Gelato",
        "price_minor": 16000,
        "tags": ["gelato"],
    },
]

CHAI_ITEMS = [
    {"sku": "chai_masala", "name": "Masala Chai", "price_minor": 8000, "tags": ["chai"]},
    {"sku": "chai_ginger", "name": "Ginger Chai", "price_minor": 8500, "tags": ["chai"]},
]


@pytest.fixture()
def gelato(tmp_path: Path):
    server = _MerchantServer("Gelateria Milano", GELATO_ITEMS, tmp_path)
    yield server
    server.stop()


@pytest.fixture()
def chai(tmp_path: Path):
    server = _MerchantServer("Chai House", CHAI_ITEMS, tmp_path)
    yield server
    server.stop()


# ---------------------------------------------------------------------------
# HttpMCPClient — one merchant
# ---------------------------------------------------------------------------


class TestHttpMCPClient:
    @pytest.mark.asyncio
    async def test_search_products_needs_no_token(self, gelato: _MerchantServer):
        client = HttpMCPClient(gelato.origin())
        result = await client.search_products("gelato")
        assert result["success"] is True
        assert client._access_token is None, "search_products must never fetch a token"
        skus = {item["sku"] for item in result["data"]["items"]}
        assert "gelato_vanilla" in skus

    @pytest.mark.asyncio
    async def test_get_order_obtains_and_uses_a_token(self, gelato: _MerchantServer):
        client = HttpMCPClient(gelato.origin())
        assert client._access_token is None
        result = await client.get_order("chk_does_not_exist")
        assert client._access_token is not None, "get_order must obtain an OAuth token"
        # Business outcome is "not found" (no such checkout), but that proves
        # the call reached handle_mcp_request authenticated, not rejected.
        assert result["success"] is False
        assert result["error"]["reason_code"] == "checkout.not_found"


# ---------------------------------------------------------------------------
# FederatingMCPClient — many merchants
# ---------------------------------------------------------------------------


class TestFederatingMCPClient:
    @pytest.mark.asyncio
    async def test_fanout_merges_and_stamps_merchant_id(
        self, gelato: _MerchantServer, chai: _MerchantServer
    ):
        client = FederatingMCPClient([gelato.origin(), chai.origin()])
        result = await client.search_products("")
        assert result["success"] is True
        items = result["data"]["items"]
        by_sku = {item["sku"]: item["merchant_id"] for item in items}
        assert by_sku["gelato_vanilla"] == "gelateria-milano"
        assert by_sku["chai_masala"] == "chai-house"
        assert result["data"]["count"] == len(items) == 4

    @pytest.mark.asyncio
    async def test_unreachable_origin_logs_and_contributes_nothing(
        self, gelato: _MerchantServer, caplog: pytest.LogCaptureFixture
    ):
        dead_port = _free_port()  # nobody is listening here
        dead = MerchantOrigin(
            name="Ghost Merchant",
            base_url=f"http://127.0.0.1:{dead_port}",
            client_id="x",
            client_secret="y",
        )
        client = FederatingMCPClient([gelato.origin(), dead], search_timeout_seconds=2.0)
        with caplog.at_level("WARNING"):
            result = await client.search_products("gelato")
        assert result["success"] is True
        skus = {item["sku"] for item in result["data"]["items"]}
        assert skus == {"gelato_vanilla", "gelato_pistachio"}
        assert any("ghost-merchant" in r.message for r in caplog.records)

    def test_client_for_returns_right_client_and_raises_on_unknown(
        self, gelato: _MerchantServer, chai: _MerchantServer
    ):
        client = FederatingMCPClient([gelato.origin(), chai.origin()])
        assert client.client_for("gelateria-milano").merchant.base_url == gelato.base_url
        assert client.client_for("chai-house").merchant.base_url == chai.base_url
        with pytest.raises(ValueError, match="Unknown merchant_id"):
            client.client_for("nonexistent-merchant")
