# Shared helpers for the demo-mode tests (not a test module).
#
# Demo mode is the only path that can run a real py_webauthn ceremony end to
# end without hardware, so both the demo tests and the evidence-completeness
# tests drive the same flow. The `demo_client` fixture lives in conftest.py;
# the config and the two ceremony helpers live here.

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from openstore.config import load_config

ROOT = Path(__file__).resolve().parent.parent
BUYER = "demobuyer0000001"
OPERATOR = {"X-Operator-Id": f"web:{BUYER}"}

POLICY = {
    "policy_version": 2,
    "currency": "INR",
    "max_spend_per_tx_minor": 100_000,
    "max_spend_total_minor": 400_000,
    "max_transactions": 10,
    "allowed_tags": ["gelato"],
    "tag_mode": "any",
    "blocked_skus": [],
    "not_before": 1_700_000_000,
    "expires_at": 1_900_000_000,
    "assertion_max_age_seconds": 86400,
    "fulfilment_mode": "all_or_nothing",
    "required_skus": [],
}


def config_dict(tmp_path: Path, *, key_id: str = "rzp_test_demo", demo: bool = True) -> dict:
    return {
        "merchant": {"name": "OpenStore Demo", "currency": "INR"},
        "razorpay": {
            "key_id": key_id,
            "key_secret": "demo_secret",
            "webhook_secret": "demo_webhook_secret",
        },
        "discord": {
            "bot_token": "",
            "buyer_trace_channel_id": 0,
            "merchant_trace_channel_id": 0,
            "money_trace_channel_id": 0,
            "alerts_channel_id": 0,
            "buyer_bot_enabled": False,
        },
        "webauthn": {
            "rp_id": "localhost",
            "rp_name": "OpenStore Demo",
            "origin": "http://localhost:8000",
        },
        "database": {"url": f"sqlite:///{tmp_path / 'demo.db'}"},
        "catalog_path": str(tmp_path / "catalog.yaml"),
        "demo_mode": demo,
    }


def write_config(tmp_path: Path, **kwargs: Any) -> Any:
    shutil.copy(ROOT / "configs" / "catalog.yaml", tmp_path / "catalog.yaml")
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(config_dict(tmp_path, **kwargs)))
    return load_config(path)


def enrol_and_sign_policy(client: TestClient, handoff_token: str) -> None:
    """The shim's half of enrolment + policy signing, done over HTTP."""
    begin = client.post(
        "/internal/webauthn/register/begin",
        json={"user_name": f"web:{BUYER}"},
        headers=OPERATOR,
    ).json()
    made = client.post("/demo/webauthn/create", json={"challenge": begin["challenge"]}).json()
    res = client.post(
        "/internal/webauthn/register/complete",
        json={
            "credential_id": made["credential_id"],
            "client_data_json": made["client_data_json"],
            "attestation_object": made["attestation_object"],
            "challenge": begin["challenge"],
        },
        headers=OPERATOR,
    )
    assert res.status_code == 200, res.text

    begin = client.post(
        "/internal/webauthn/assertion/begin", json={"mode": "policy"}, headers=OPERATOR
    ).json()
    got = client.post("/demo/webauthn/get", json={"challenge": begin["challenge"]}).json()
    res = client.post(
        "/internal/webauthn/assertion/complete",
        json={
            **got,
            "challenge": begin["challenge"],
            "policy": POLICY,
            "handoff_token": handoff_token,
        },
        headers=OPERATOR,
    )
    assert res.status_code == 200, res.text


def approve_cart(client: TestClient, cart_id: str, token: str) -> dict[str, Any]:
    """The per-cart passkey tap: the challenge is issued inline on the page."""
    page = client.get(f"/intent/studio?token={token}")
    begin = json.loads(re.search(r"const ASSERTION_BEGIN = (\{.*?\});", page.text, re.S).group(1))
    got = client.post("/demo/webauthn/get", json={"challenge": begin["challenge"]}).json()
    res = client.post(
        f"/intent/cart/{cart_id}/approve",
        json={**got, "token": token, "challenge": begin["challenge"]},
    )
    assert res.status_code == 200, res.text
    return dict(res.json())


def buy(client: TestClient, qty: int = 2) -> dict[str, Any]:
    """Cart -> policy -> per-cart approval -> pay. Returns the shop result."""
    items = [{"sku": "gelato_vanilla", "qty": qty}]
    res = client.post("/web/cart", json={"buyer_key": BUYER, "items": items})
    enrol_and_sign_policy(client, res.json()["signin_url"].split("token=")[1])

    body = client.post("/web/cart", json={"buyer_key": BUYER, "items": items}).json()
    shop = approve_cart(client, body["cart_id"], body["approval_url"].split("token=")[1])[
        "shop_result"
    ]
    link_id = shop["short_url"].rsplit("/", 1)[-1]
    assert client.post(f"/demo/pay/{link_id}?action=paid").status_code == 200
    return dict(shop)
