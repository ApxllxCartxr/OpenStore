# Q-050 / DECISION-051: a bundle is as complete as its ceremony was.
#
# Before this, every bundle the webhook path produced failed 5 of the offline
# verifier's 14 checks — not because anything was wrong, but because three
# facts that existed at authorization time were gone by bundle time: the
# verified assertion, the ALLOW transcript, and (once those two land) an AAL
# the bundle could actually prove. These tests pin all three, and pin that a
# checkout authorized WITHOUT a per-cart tap still builds the bundle it built
# before rather than a fabricated one.
#
# The flow is driven through demo mode because that is the one path that can
# run a real py_webauthn ceremony end to end without hardware; what is being
# tested is the production evidence path, which demo mode does not alter.

from __future__ import annotations

from typing import Any

from demo_harness import BUYER, POLICY, buy
from fastapi.testclient import TestClient
from openstore.verify.checks import VerifierContext, run_all_checks

EXPECTED_TRANSCRIPT = [
    "human_authority_present",
    "currency_match",
    "merchant_lock",
    "policy_not_before",
    "policy_expiry",
    "transaction_count",
    "item_qty",
    "item_blocked_sku",
    "item_tag_allowlist",
    "spend_per_tx",
    "spend_envelope",
    "spend_cumulative",
    "campaign_validity",
]


def _bundle(client: TestClient, checkout_id: str) -> dict[str, Any]:
    res = client.get(f"/orders/{checkout_id}/evidence?buyer_key={BUYER}")
    assert res.status_code == 200, res.text
    return dict(res.json())


class TestOfflineVerification:
    def test_every_check_passes(self, demo_client):
        """The whole point: openstore-verify on a bundle this tree produced."""
        bundle = _bundle(demo_client, buy(demo_client)["checkout_id"])
        results = run_all_checks(VerifierContext(bundle=bundle))

        failed = {r.name: r.detail for r in results if not r.passed}
        assert failed == {}, f"offline verifier rejected a real bundle: {failed}"
        assert len(results) == 14

    def test_aal_is_recomputed_not_copied_from_the_checkout(self, demo_client):
        """holdcancel grades an amount-banded risk tier (2 here); the bundle
        states what its own predicates prove (3, cart-bound assertion). Copying
        the risk tier is what used to fail check 12."""
        shop = buy(demo_client)
        bundle = _bundle(demo_client, shop["checkout_id"])

        assert shop["aal_level"] == 2, "the money-path risk tier"
        assert bundle["aal"]["level"] == 3, "what the evidence proves"
        assert bundle["aal"]["predicates"]["e5"] is True, "cart-bound assertion"


class TestPersistedAssertion:
    def test_the_bundle_carries_the_verified_assertion(self, demo_client):
        bundle = _bundle(demo_client, buy(demo_client)["checkout_id"])
        webauthn = bundle["authority"]["webauthn"]

        assert webauthn["signature"], "e2/check 5 need the signature"
        assert webauthn["authenticator_data"], "check 7 reads the UV bit from here"
        assert webauthn["signed_at"], "e3 measures adjudication against this"
        assert webauthn["assertion_max_age_seconds"] == POLICY["assertion_max_age_seconds"]

    def test_challenge_binding_matches_the_goods(self, demo_client):
        """A cart-bound assertion that named a different cart is exactly what
        check 6 exists to catch, so the binding must be the real one."""
        bundle = _bundle(demo_client, buy(demo_client)["checkout_id"])
        binding = bundle["authority"]["webauthn"]["challenge_binding"]

        assert binding["mode"] == "cart"
        assert binding["cart_hash"] == bundle["goods"]["cart_hash"]

    def test_uv_bit_is_set_in_the_persisted_authenticator_data(self, demo_client):
        import base64

        bundle = _bundle(demo_client, buy(demo_client)["checkout_id"])
        raw = bundle["authority"]["webauthn"]["authenticator_data"]
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

        assert decoded[32] & 0x04, "user verification"


class TestPersistedTranscript:
    def test_transcript_is_the_decision_that_authorized_this_checkout(self, demo_client):
        bundle = _bundle(demo_client, buy(demo_client)["checkout_id"])
        transcript = bundle["adjudication"]["transcript"]

        assert [entry["check"] for entry in transcript] == EXPECTED_TRANSCRIPT
        assert all(entry["result"] == "pass" for entry in transcript)
        assert all(entry["reason_code"] is None for entry in transcript)

    def test_both_facts_land_on_the_checkout_row(self, demo_client):
        """Persisted at creation, not replayed at bundle time: a replay would
        read a cumulative spend that has since moved on, so the row has to
        hold the decision actually taken."""
        import openstore.core.database as db
        from openstore.models import Checkout
        from sqlmodel import Session, select

        checkout_id = buy(demo_client)["checkout_id"]
        with Session(db._engine) as session:
            row = session.exec(select(Checkout).where(Checkout.id == checkout_id)).first()

        assert row is not None
        assert [e["check"] for e in row.decision_transcript] == EXPECTED_TRANSCRIPT
        assert row.webauthn_assertion["challenge_binding"]["mode"] == "cart"
        assert row.webauthn_assertion["signature"]


class TestNullTolerance:
    def test_a_checkout_without_a_per_cart_tap_still_builds_a_bundle(self, demo_client):
        """The agent/chat paths hold no per-cart assertion. Their bundles must
        stay exactly as complete as they were — never fabricated up."""
        from openstore.core.poai import compute_aal_level_from_bundle

        sections = {
            "transaction": {
                "checkout_id": "chk_x",
                "amount_minor": 1000,
                "merchant_id": "m",
                "currency": "INR",
            },
            "authority": {
                "scheme": "native_webauthn",
                "policy": {"policy_id": "p", "policy_version": 2, "policy_hash": "h"},
                # What a policy-only ceremony can honestly assert.
                "webauthn": {"credential_id": "cred"},
            },
            "goods": {"items": [], "cart_hash": "c", "catalog_attestations_valid": True},
            "adjudication": {"verdict": "ALLOW", "transcript": [], "evaluated_at": "2026-01-01Z"},
        }
        assert compute_aal_level_from_bundle(sections) == 0, "no assertion, no AAL"
