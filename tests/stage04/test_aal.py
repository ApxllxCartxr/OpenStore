# tests/stage04/test_aal.py
# S4.2: verify AAL predicate evaluation, level resolution, and liability strings.

from __future__ import annotations

import copy
import json
from pathlib import Path

from openstore.core.poai import (
    AAL_LIABILITY,
    compute_aal_level_from_bundle,
    evaluate_aal_predicates,
    get_aal_liability_sentence,
)

GOLDEN = Path(__file__).resolve().parent.parent.parent / "GOLDEN" / "poai"


def _load(name: str) -> dict:
    with open(GOLDEN / f"{name}.json") as f:
        return json.load(f)


class TestEvaluateAALPredicates:
    def test_aal2_bundle_predicates(self):
        bundle = _load("bundle_aal2")
        p = evaluate_aal_predicates(bundle)
        assert p["e1"] is True  # agent has client_id, scopes, token_jti, checkout:confirm
        assert p["e2"] is True  # UV bit set in authenticator_data
        assert p["e3"] is True  # assertion within max_age
        assert p["e4"] is True  # UV bit set (same as e2)
        assert p["e5"] is False  # policy-mode challenge (not cart-bound)
        assert p["e6"] is True  # catalog_attestations_valid flag set
        assert p["e7"] is True  # verdict=ALLOW with transcript
        assert p["e8"] is True  # request_digest == sha256(request_text)
        assert p["e9"] is True  # notification has receipt_digest

    def test_aal3_bundle_predicates(self):
        bundle = _load("bundle_aal3")
        p = evaluate_aal_predicates(bundle)
        assert p["e1"] is True
        assert p["e2"] is True
        assert p["e5"] is True  # cart-bound challenge
        assert p["e7"] is True

    def test_request_digest_mismatch_makes_e8_false(self):
        bundle = _load("bundle_aal2")
        tampered = copy.deepcopy(bundle)
        tampered["human_intent"]["request_text"] = "Buy pizza"
        p = evaluate_aal_predicates(tampered)
        assert p["e8"] is False

    def test_missing_uv_makes_e2_and_e4_false(self):
        bundle = _load("bundle_aal2")
        tampered = copy.deepcopy(bundle)
        # Strip UV bit from authenticator_data
        import base64
        auth_raw = tampered["authority"]["webauthn"]["authenticator_data"]
        auth_bytes = bytearray(base64.urlsafe_b64decode(auth_raw + "=="))
        auth_bytes[32] &= ~0x04  # clear UV bit
        tampered["authority"]["webauthn"]["authenticator_data"] = base64.urlsafe_b64encode(bytes(auth_bytes)).decode().rstrip("=")
        p = evaluate_aal_predicates(tampered)
        assert p["e2"] is False
        assert p["e4"] is False


class TestComputeAALLevel:
    def test_aal2_bundle_level(self):
        bundle = _load("bundle_aal2")
        assert compute_aal_level_from_bundle(bundle) == 2

    def test_aal3_bundle_level(self):
        bundle = _load("bundle_aal3")
        assert compute_aal_level_from_bundle(bundle) == 3

    def test_no_authority_returns_aal0(self):
        empty = {"transaction": None, "human_intent": None, "authority": None,
                 "goods": None, "agent": None, "adjudication": None,
                 "notification": None, "aal": None, "campaign": None}
        assert compute_aal_level_from_bundle(empty) == 0


class TestLiabilityStrings:
    """Liability strings are a closed set (DECISIONS §11.1.6)."""

    def test_aal3_liability_prefix(self):
        s = get_aal_liability_sentence(3)
        assert s.startswith("Proposed liability position (not a network rule): ")
        assert "human's authenticator signed this exact cart with user verification" in s

    def test_aal2_liability_prefix(self):
        s = get_aal_liability_sentence(2)
        assert s.startswith("Proposed liability position (not a network rule): ")
        assert "standing policy" in s

    def test_aal1_liability_prefix(self):
        s = get_aal_liability_sentence(1)
        assert s.startswith("Proposed liability position (not a network rule): ")
        assert "contested" in s

    def test_aal0_liability_prefix(self):
        s = get_aal_liability_sentence(0)
        assert s.startswith("Proposed liability position (not a network rule): ")
        assert "no verifiable human authority" in s

    def test_unknown_level_returns_sentinel(self):
        s = get_aal_liability_sentence(99)
        assert s == "Unknown AAL level"

    def test_aal_levels_closed_set(self):
        """REGISTRY.json has aal_levels = [0,1,2,3]; AAL_LIABILITY has 4 entries."""
        assert set(AAL_LIABILITY.keys()) == {0, 1, 2, 3}
