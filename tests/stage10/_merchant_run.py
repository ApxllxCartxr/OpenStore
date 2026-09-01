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
from sqlmodel import select


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
    elif op == "seed_pending_link":
        # SID-4: simulate a process that got the Razorpay payment link created
        # (psp_payment_link_id persisted) but was killed before the buyer paid.
        # The intent is HELD (policy passed, awaiting payment) — the crash left
        # it mid-flight.
        now = datetime.now(UTC)
        pid = f"pol_{merchant_id.split('-')[0]}"
        make_policy(session, merchant_id, pid)
        ckid = f"chk_{merchant_id.split('-')[0]}"
        ck = Checkout(
            id=ckid, trace_id=f"tr_{merchant_id.split('-')[0]}",
            client_id=f"cli_{merchant_id.split('-')[0]}", merchant_id=merchant_id,
            cart_hash=f"h_{merchant_id.split('-')[0]}", cart_version=1,
            amount_minor=40000, currency="INR", state=OrderState.HELD,
            policy_id=pid, policy_hash=f"ph_{pid}", aal_level=1,
            expires_at=now + timedelta(hours=1), idempotency_key=f"idem_{merchant_id.split('-')[0]}",
            cart_snapshot={"items": []}, created_at=now, updated_at=now,
            psp_provider="razorpay", psp_order_id=ckid,
            psp_payment_link_id=f"plink_{merchant_id.split('-')[0]}",
        )
        session.add(ck)
        session.commit()
        print(ck.id)
    elif op == "recover_no_duplicate":
        # SID-4 restart: re-invoke create_payment_link for the same (HELD) checkout.
        # The mock Razorpay store already holds ONE link keyed by reference_id; a
        # second create fires the duplicate-reference_id recovery path — no
        # duplicate Razorpay object and no orphaned non-terminal checkout.
        from openstore.core.database import get_session as _gs
        from openstore.psp import razorpay_driver as driver

        class _MockStore:
            def __init__(self) -> None:
                self.links: dict[str, dict] = {}
                self.created: list[str] = []

            def create(self, req: dict) -> dict:
                ref = req.get("reference_id", "")
                if ref in self.links:
                    # Razorpay's literal duplicate-reference_id error code.
                    raise RuntimeError("REFERENCE_ID_ALREADY_EXISTS")
                link = dict(req)
                link["id"] = f"plink_{ref[:20]}"
                self.links[ref] = link
                self.created.append(ref)
                return dict(link)

            def all(self, params: dict) -> dict:
                ref = params.get("reference_id", "")
                items = [dict(self.links[ref])] if ref in self.links else []
                return {"items": items}

        store = _MockStore()
        # Pre-load the store with the link created in the previous (killed) process.
        ckid = f"chk_{merchant_id.split('-')[0]}"
        store.create({"reference_id": ckid})

        class _MockRz:
            def __init__(self, store: _MockStore) -> None:
                self.payment_link = store

        checkout = session.exec(
            select(Checkout).where(Checkout.id == ckid)
        ).first()
        assert checkout is not None, "checkout must survive the crash"
        session.close()

        s2 = _gs(cfg)
        try:
            driver.create_payment_link(
                config=cfg, session=s2, trace_id=f"tr_restart_{merchant_id.split('-')[0]}",
                client_id="restart", checkout_id=ckid, amount_minor=40000,
                currency="INR", description="retry-after-crash",
                mock_razorpay=_MockRz(store),
            )
            s2.commit()
        finally:
            s2.close()

        s3 = _gs(cfg)
        try:
            refreshed = s3.exec(
                select(Checkout).where(Checkout.id == ckid)
            ).first()
            # Exactly one link exists in the mock store (no duplicate Razorpay object).
            assert len(store.links) == 1, f"duplicate Razorpay link: {list(store.links)}"
            assert store.links[ckid]["id"] == refreshed.psp_payment_link_id
            # No orphaned non-terminal checkout left without a payment link.
            nonterminal = s3.exec(
                select(Checkout).where(
                    Checkout.merchant_id == merchant_id,
                    Checkout.state.in_([OrderState.HELD]),
                )
            ).all()
            assert all(c.psp_payment_link_id for c in nonterminal), "orphaned checkout"
            print("recovered", refreshed.psp_payment_link_id)
        finally:
            s3.close()
    session.close()


if __name__ == "__main__":
    main()
