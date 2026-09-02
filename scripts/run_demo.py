#!/usr/bin/env python3
"""scripts/run_demo.py — end-to-end OpenStore demo.

Walks the full real flow live (no stubs):
  1. Load or write demo merchant config + catalog.
  2. Virtual-authenticator registration + policy-signing ceremony
     (real WebAuthn, one human authorization → no_human_authority=True
     policy with bounded caps).
  3. Construct a BuyerAgent backed by a real InProcessMCPClient (real OAuth
     ES256 JWT, real DB, in-process dispatch into handle_mcp_request).
  4. Run a real shop("vanilla gelato") → full compiler transcript + AAL +
     (if Razorpay test creds present) real Razorpay test-mode short URL.
  5. Run a second shop() designed to fail (over the per-tx cap) → exact
     reason code from the transcript.
  6. If Razorpay test creds are present, simulate the payment_link.paid
     webhook payload (HMAC-signed, run through the real psp.router
     signature verification) → build a PoAI bundle → verify it.
  7. If creds are absent, say so plainly and stop after step 5.

Usage:
    uv run python scripts/run_demo.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add src to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from openstore.agents.buyer_agent import BuyerAgent
from openstore.agents.mcp_client import InProcessMCPClient
from openstore.config import Settings
from openstore.core.database import get_session, init_database
from openstore.core.policy_signing import compute_policy_hash
from openstore.core.webauthn_rp import (
    begin_registration,
    complete_registration,
    create_policy_signing_challenge,
)
from openstore.devtools.virtual_authenticator import generate_es256_authenticator
from openstore.models import IntentPolicy, WebAuthnCredential


def _have_razorpay_creds(config: Settings) -> bool:
    """True if Razorpay test-mode creds are present in the environment."""
    kid = config.razorpay.key_id
    sec = config.razorpay.key_secret
    return bool(kid) and not kid.startswith("$") and bool(sec) and not sec.startswith("$")


def _load_or_create_demo_config() -> Settings:
    """Load gelateria.yaml, or build a demo config from the CLI helper."""
    config_path = ROOT / "gelateria.yaml"
    if not config_path.exists():
        from openstore.cli import build_config_dict
        import yaml

        cfg = build_config_dict("Gelateria Milano", "INR")
        config_path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    catalog_path = ROOT / "catalog.yaml"
    if not catalog_path.exists():
        catalog_path.write_text(
            "- sku: gelato_vanilla\n  name: Vanilla Gelato\n"
            "  price_minor: 15000\n  tags: [vegan, gelato]\n  stock: 100\n"
        )

    from openstore.config import load_config

    return load_config(str(config_path))


def _run_policy_signing_ceremony(config: Settings) -> tuple[str, str]:
    """One-time human authorization: register a credential, sign an
    IntentPolicy with no_human_authority=True and bounded caps.

    Real py_webauthn-verified ceremony via VirtualAuthenticator.
    Returns (policy_id, merchant_id) so the demo can use both.
    """
    session = get_session(config)
    try:
        merchant_id = config.merchant.name.lower().replace(" ", "-")
        fields = {
            "policy_version": 2,
            "currency": "INR",
            "max_spend_per_tx_minor": 50_000,
            "max_spend_total_minor": 200_000,
            "max_transactions": 1,
            "allowed_tags": ["gelato", "cone", "topping", "vegan"],
            "tag_mode": "all",
            "blocked_skus": [],
            "not_before": int(time.time()) - 60,
            "expires_at": int(time.time()) + 30 * 24 * 3600,
            "assertion_max_age_seconds": 86400,
            "fulfilment_mode": "all_or_nothing",
            "required_skus": [],
            "no_human_authority": True,
        }
        policy_hash = compute_policy_hash({**fields, "merchant_id": merchant_id})
        policy_id = (
            "pol_" + hashlib.sha256((merchant_id + ":" + policy_hash).encode()).hexdigest()[:24]
        )

        # Register a virtual authenticator + credential via real py_webauthn
        va = generate_es256_authenticator(
            rp_id=config.webauthn.rp_id, origin=config.webauthn.origin
        )
        reg_options = begin_registration(
            config,
            user_handle=merchant_id,
            user_name=merchant_id,
            display_name=merchant_id,
        )
        challenge = reg_options["challenge"]
        from openstore.devtools.virtual_authenticator import b64u_raw

        challenge_bytes = b64u_raw(challenge)
        reg = va.register(challenge_bytes)
        cred = complete_registration(
            session=session,
            config=config,
            user_handle=merchant_id,
            credential_id=reg.credential_id,
            client_data_json=reg.client_data_json,
            attestation_object=reg.attestation_object,
            challenge_b64url=challenge,
        )
        print(f"  registered credential: {cred.credential_id[:20]}...")

        # Sign the policy with a policy-mode assertion (real py_webauthn)
        sign_chal = create_policy_signing_challenge(
            config,
            policy_id=policy_id,
            policy_hash=policy_hash,
            merchant_id=merchant_id,
        )["challenge"]
        sign_bytes = b64u_raw(sign_chal)
        asr = va.assert_credential(sign_bytes, sign_count=2)

        from openstore.core.webauthn_rp import complete_assertion

        verified, sign_count = complete_assertion(
            session=session,
            config=config,
            user_handle=merchant_id,
            credential_id=asr.credential_id,
            client_data_json=asr.client_data_json,
            authenticator_data=asr.authenticator_data,
            signature=asr.signature,
            challenge_b64url=sign_chal,
            binding={"mode": "policy"},
        )
        if not verified:
            raise RuntimeError("policy-signing assertion failed to verify")

        # Persist the signed policy
        policy = IntentPolicy(
            id=policy_id,
            merchant_id=merchant_id,
            policy_version=2,
            policy_hash=policy_hash,
            currency="INR",
            max_spend_per_tx_minor=fields["max_spend_per_tx_minor"],
            max_spend_total_minor=fields["max_spend_total_minor"],
            max_transactions=fields["max_transactions"],
            allowed_tags=fields["allowed_tags"],
            tag_mode=fields["tag_mode"],
            blocked_skus=fields["blocked_skus"],
            not_before=fields["not_before"],
            expires_at=fields["expires_at"],
            assertion_max_age_seconds=fields["assertion_max_age_seconds"],
            fulfilment_mode=fields["fulfilment_mode"],
            required_skus=fields["required_skus"],
            no_human_authority=True,
            webauthn_credential_id=asr.credential_id,
            webauthn_sign_count=sign_count,
            signed_at=datetime.now(UTC).replace(tzinfo=None),
            is_active=True,
        )
        session.add(policy)
        session.commit()
        print(f"  signed policy: {policy_id} (no_human_authority=True)")
        return policy_id, merchant_id
    finally:
        session.close()


def _print_step(n: int, label: str) -> None:
    print(f"\n--- step {n}: {label} ---")


async def _run_shop_step(
    agent: BuyerAgent,
    config: Settings,
    goal: str,
    policy_id: str,
    trace_id: str,
) -> dict:
    """Run the buyer agent's real cart-compile step for `goal`.

    checkout_initiate mints a real Razorpay payment link (no stub) — it is
    only invoked when test-mode creds are present, so the compiler transcript
    and AAL are still real and visible without any Razorpay account.
    """
    if _have_razorpay_creds(config):
        return await agent.shop(goal, policy_id=policy_id, trace_id=trace_id)

    plan_result = await agent.plan(goal, policy_id, trace_id)
    cart = plan_result.get("cart", [])
    if not cart:
        return {"allowed": False, "reason_code": "buyer.no_cart", "trace_id": trace_id}

    from openstore.agents.buyer_agent import compute_cart_hash

    cart_result = await agent.mcp.call(
        "create_cart",
        {
            "merchant_id": config.merchant.name.lower().replace(" ", "-"),
            "items": cart,
            "policy_id": policy_id,
            "cart_hash": compute_cart_hash(cart),
            "cart_version": 1,
        },
    )
    if not cart_result.get("success"):
        return {
            "allowed": False,
            "reason_code": cart_result.get("error", {}).get("reason_code", "buyer.cart_failed"),
            "trace_id": trace_id,
        }
    data = cart_result.get("data", {})
    return {
        "allowed": data.get("allowed"),
        "reason_code": data.get("reason_code"),
        "trace_id": trace_id,
        "checkout_id": data.get("checkout_id"),
        "aal_level": data.get("aal_level"),
        "transcript": data.get("transcript"),
        "effective_amount_minor": data.get("effective_amount_minor"),
    }


def main() -> int:
    print("OpenStore end-to-end demo")

    _print_step(1, "load demo config + init DB")
    config = _load_or_create_demo_config()
    init_database(config)
    print(f"  merchant: {config.merchant.name}")
    print(f"  razorpay creds present: {_have_razorpay_creds(config)}")

    _print_step(2, "policy-signing ceremony (one human authorization)")
    policy_id, merchant_id = _run_policy_signing_ceremony(config)

    _print_step(3, "construct BuyerAgent with real InProcessMCPClient")
    mcp = InProcessMCPClient(config)
    agent = BuyerAgent(config, mcp)
    print("  agent ready, OAuth token issued")

    _print_step(4, 'shop("vanilla gelato") — autonomous AAL0 purchase')
    result = asyncio.run(
        _run_shop_step(
            agent,
            config,
            "vanilla gelato",
            policy_id,
            "trace_demo_happy",
        )
    )
    print(f"  allowed: {result.get('allowed')}")
    print(f"  reason_code: {result.get('reason_code')}")
    print(f"  aal_level: {result.get('aal_level')}")
    print(f"  effective_amount_minor: {result.get('effective_amount_minor')}")
    if result.get("short_url"):
        print(f"  short_url: {result.get('short_url')}")
        print(f"  cancel_token: {result.get('cancel_token')[:16]}...")
    transcript = result.get("transcript") or []
    print(f"  transcript ({len(transcript)} checks):")
    for entry in transcript:
        print(
            f"    [{entry.get('result')}] {entry.get('check')}"
            + (f" ({entry.get('reason_code')})" if entry.get("reason_code") else "")
        )

    _print_step(5, "shop again over the policy's tx-count cap — must fail with reason code")
    big_result = asyncio.run(
        _run_shop_step(
            agent,
            config,
            "vanilla gelato",
            policy_id,
            "trace_demo_fail",
        )
    )
    print(f"  allowed: {big_result.get('allowed')}")
    print(f"  reason_code: {big_result.get('reason_code')}")

    if not _have_razorpay_creds(config):
        print("\nno RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET in environment.")
        print("steps 6-7 (webhook simulation + PoAI bundle) skipped.")
        return 0

    _print_step(6, "simulate payment_link.paid webhook (real HMAC + verify)")
    short_url = result.get("short_url")
    checkout_id = result.get("checkout_id")
    if not short_url or not checkout_id:
        print("  no payment link from step 4 — skipping webhook")
        return 0

    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": result.get("checkout_id"),
                    "reference_id": checkout_id,
                    "amount": result.get("effective_amount_minor", 15000),
                    "currency": "INR",
                    "status": "paid",
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    secret = config.razorpay.webhook_secret or "test_secret"
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    from openstore.psp.router import process_webhook

    session = get_session(config)
    try:
        process_webhook(session, raw, sig, secret, trace_id="trace_demo_hook")
        session.commit()
        print(f"  webhook dispatched for checkout {checkout_id[:16]}...")
    except Exception as e:
        print(f"  webhook dispatch error: {e}")
    finally:
        session.close()

    _print_step(7, "build + verify PoAI bundle")
    try:
        from openstore.core.poai import build_poai_bundle
        from openstore.verify.verifier import verify_poai_bundle

        session = get_session(config)
        try:
            bundle = build_poai_bundle(session, checkout_id=checkout_id)
        finally:
            session.close()
        result = verify_poai_bundle(bundle)
        print(f"  bundle verification: {result}")
    except Exception as e:
        print(f"  PoAI bundle error: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
