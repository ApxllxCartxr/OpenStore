# tests/stage10/_merchant_run.py
# Helper executed in a SUBPROCESS to simulate a single `openstore serve <cfg>` install.
# Each subprocess runs in its own interpreter, so it owns its own module-global DB engine
# and its own on-disk SQLite file — mirroring one merchant process in production.
#
# Usage: python _merchant_run.py <db_path> <merchant_id> seed_policy
#        python _merchant_run.py <db_path> <merchant_id> seed_bundle
#        python _merchant_run.py <db_path> <merchant_id> read_policies
#        python _merchant_run.py <db_path> <merchant_id> read_spend <policy_id>
#        python _merchant_run.py <db_path> <merchant_id> read_campaigns

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from sqlmodel import select

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
from openstore.core.database import compute_policy_spend, get_session, init_database
from openstore.core.ledger import create_capture_entry, create_reserve_entry
from openstore.models import Campaign, Checkout, IntentPolicy, OrderState


def build_settings(db_url: str, merchant_id: str) -> Settings:
    return Settings(
        merchant=MerchantConfig(name=merchant_id, currency="INR"),
        razorpay=RazorpayConfig(key_id="rzp_test_xxx", key_secret="s"),
        discord=DiscordConfig(
            bot_token="token", buyer_trace_channel_id=1, merchant_trace_channel_id=2,
            money_trace_channel_id=3, alerts_channel_id=4,
        ),
        webauthn=WebAuthnConfig(rp_id="localhost", rp_name="OS", origin="http://localhost"),
        database=DatabaseConfig(url=db_url),
        llm=LLMSettings(),
        campaign=CampaignSettings(),
    )


def make_policy(session, merchant_id: str, pid: str) -> IntentPolicy:
    now = datetime.now(UTC)
    pol = IntentPolicy(
        id=pid, merchant_id=merchant_id, policy_version=2, policy_hash=f"ph_{pid}",
        currency="INR", max_spend_per_tx_minor=50000, max_spend_total_minor=200000,
        max_transactions=10, allowed_tags=[], tag_mode="all", blocked_skus=[],
        not_before=int(now.timestamp()) - 60, expires_at=int(now.timestamp()) + 86400,
        assertion_max_age_seconds=86400, fulfilment_mode="all_or_nothing",
        required_skus=[], webauthn_credential_id=f"cred_{pid}", webauthn_sign_count=0,
        signed_at=now, is_active=True,
    )
    session.add(pol)
    return pol


def main() -> None:
    db_path, merchant_id, op = sys.argv[1], sys.argv[2], sys.argv[3]
    cfg = build_settings(f"sqlite:///{db_path}", merchant_id)
    init_database(cfg)
    session = get_session(cfg)

    if op == "seed_policy":
        make_policy(session, merchant_id, f"pol_{merchant_id.split('-')[0]}")
        session.commit()
    elif op == "seed_bundle":
        now = datetime.now(UTC)
        pid = f"pol_{merchant_id.split('-')[0]}"
        make_policy(session, merchant_id, pid)
        ck = Checkout(
            id=f"chk_{merchant_id.split('-')[0]}", trace_id=f"tr_{merchant_id.split('-')[0]}",
            client_id=f"cli_{merchant_id.split('-')[0]}", merchant_id=merchant_id,
            cart_hash=f"h_{merchant_id.split('-')[0]}", cart_version=1,
            amount_minor=40000, currency="INR", state=OrderState.RELEASED,
            policy_id=pid, policy_hash=f"ph_{pid}", aal_level=2,
            expires_at=now + timedelta(hours=1), idempotency_key=f"idem_{merchant_id.split('-')[0]}",
            cart_snapshot={"items": []}, created_at=now, updated_at=now,
        )
        session.add(ck)
        create_reserve_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
        create_capture_entry(session, ck.trace_id, ck.client_id, ck.id, 40000)
        session.commit()
    elif op == "read_policies":
        rows = list(session.exec(select(IntentPolicy)).all())
        print([(r.id, r.merchant_id) for r in rows])
    elif op == "read_spend":
        pid = sys.argv[4]
        print(compute_policy_spend(session, pid))
    elif op == "read_campaigns":
        rows = list(session.exec(select(Campaign)).all())
        print([(c.id, c.merchant_id, c.title) for c in rows])
    session.close()


if __name__ == "__main__":
    main()
