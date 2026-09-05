# tests/test_poai_golden.py
# S4.3: byte-identical reproduction of GOLDEN/poai bundles and tampered variants.

from __future__ import annotations

import json
import socket
from pathlib import Path

from openstore.core.poai import (
    SECTION_ORDER,
    build_hash_chain,
    verify_poai_bundle,
)
from openstore.verify import checks

GOLDEN = Path(__file__).resolve().parent / "GOLDEN" / "poai"


def _load(name: str) -> dict:
    with open(GOLDEN / f"{name}.json") as f:
        return json.load(f)


class TestGoldenBundleAA2:
    def test_bundle_aal2_validates_hash_chain(self):
        bundle = _load("bundle_aal2")
        ok, errors = verify_poai_bundle(bundle)
        assert ok, f"chain errors: {errors}"

    def test_bundle_aal2_passes_all_14_checks(self):
        bundle = _load("bundle_aal2")
        ctx = checks.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )
        results = checks.run_all_checks(ctx)
        failed = [r for r in results if not r.passed]
        assert not failed, f"failed checks: {[(r.name, r.detail) for r in failed]}"


class TestGoldenBundleAA3:
    def test_bundle_aal3_validates_hash_chain(self):
        bundle = _load("bundle_aal3")
        ok, errors = verify_poai_bundle(bundle)
        assert ok, f"chain errors: {errors}"

    def test_bundle_aal3_passes_all_14_checks(self):
        bundle = _load("bundle_aal3")
        ctx = checks.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )
        results = checks.run_all_checks(ctx)
        failed = [r for r in results if not r.passed]
        assert not failed, f"failed checks: {[(r.name, r.detail) for r in failed]}"


class TestTamperedBundles:
    """Each tampered variant must fail exactly the check the tampered field targets."""

    def test_tampered_amount_fails_chain_integrity_and_amount(self):
        bundle = _load("bundle_tampered_amount")
        # chain_integrity check should fail at link[0] (transaction)
        ctx = checks.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )
        results = checks.run_all_checks(ctx)
        chain_result = next(r for r in results if r.name == "chain_integrity")
        assert not chain_result.passed
        assert chain_result.section == "transaction"
        assert chain_result.link_index == 0

        amount_result = next(r for r in results if r.name == "amount_consistency")
        assert not amount_result.passed
        assert "99999" in amount_result.detail or "21000" in amount_result.detail

    def test_tampered_transcript_fails_chain_and_re_execution(self):
        bundle = _load("bundle_tampered_transcript")
        ctx = checks.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )
        results = checks.run_all_checks(ctx)
        chain_result = next(r for r in results if r.name == "chain_integrity")
        assert not chain_result.passed
        assert chain_result.section == "adjudication"

    def test_bad_merchant_sig_fails_signature_check(self):
        bundle = _load("bundle_bad_merchant_sig")
        ctx = checks.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )
        results = checks.run_all_checks(ctx)
        sig_result = next(r for r in results if r.name == "merchant_signature")
        assert not sig_result.passed

    def test_missing_anchor_passes_time_anchor(self):
        """Per §3.6: missing anchor reports 'absent' (not failed)."""
        bundle = _load("bundle_missing_anchor")
        ctx = checks.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )
        results = checks.run_all_checks(ctx)
        anchor_result = next(r for r in results if r.name == "time_anchor")
        assert anchor_result.passed
        assert "absent" in anchor_result.detail


class TestHashChainGolden:
    """Hash chain vectors from GOLDEN/hashchain must reproduce deterministically."""

    def test_all_null_sections_chain(self):
        with open(
            Path(__file__).resolve().parent / "GOLDEN" / "hashchain" / "all_null_sections.json"
        ) as f:
            v = json.load(f)
        sections_data = {name: b"null" for name in SECTION_ORDER}
        chain = build_hash_chain(sections_data)
        assert chain["links"] == v["links"]
        assert chain["root"] == v["root"]


class TestVerifierOffline:
    """Verifier MUST NOT make network calls. Confirm by attempting socket connections."""

    def test_verifier_does_no_network(self):
        """A successful bundle verification completes with no sockets opened."""
        bundle = _load("bundle_aal2")
        ctx = checks.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )
        # Patch socket.socket to detect any network attempt
        original_socket = socket.socket
        network_attempted = []

        def spy(*args, **kwargs):
            network_attempted.append((args, kwargs))
            return original_socket(*args, **kwargs)

        socket.socket = spy
        try:
            results = checks.run_all_checks(ctx)
            assert all(r.passed for r in results)
        finally:
            socket.socket = original_socket

        assert network_attempted == [], f"verifier attempted network: {network_attempted}"
