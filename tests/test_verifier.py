# tests/test_verifier.py
# S4.4: integration tests for the openstore-verify CLI.

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "GOLDEN" / "poai"


def _run_cli(args, expect_exit: int) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-m", "openstore.verify", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
    )


def _load(name: str) -> dict:
    with open(GOLDEN / f"{name}.json") as f:
        return json.load(f)


class TestValidBundle:
    def test_aal2_bundle_exits_0(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_aal2.json",
            "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
        ], expect_exit=0)
        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
        assert "AAL level: 2" in result.stdout

    def test_aal3_bundle_exits_0(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_aal3.json",
            "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
        ], expect_exit=0)
        assert result.returncode == 0, f"stderr: {result.stderr}"


class TestTamperedBundles:
    def test_tampered_amount_exits_1_with_section_name(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_tampered_amount.json",
        ], expect_exit=1)
        assert result.returncode == 1
        # Per DONE WHEN: output names section 'transaction' and the broken link index
        assert "transaction" in result.stdout
        assert "link[0]" in result.stdout or "link_index=0" in result.stdout

    def test_tampered_transcript_exits_1(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_tampered_transcript.json",
            "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
        ], expect_exit=1)
        assert result.returncode == 1

    def test_bad_merchant_sig_exits_1(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_bad_merchant_sig.json",
            "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
        ], expect_exit=1)
        assert result.returncode == 1
        assert "merchant_signature" in result.stdout

    def test_missing_anchor_passes_with_absent_marker(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_missing_anchor.json",
            "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
        ], expect_exit=0)
        assert result.returncode == 0
        assert "absent" in result.stdout


class TestJSONOutput:
    def test_json_emits_merchant_asserted(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_aal2.json",
            "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
            "--json",
        ], expect_exit=0)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "results" in data
        assert "merchant_asserted" in data
        assert data["aal"] == 2

    def test_json_has_14_results(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_aal2.json",
            "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
            "--json",
        ], expect_exit=0)
        data = json.loads(result.stdout)
        assert len(data["results"]) == 14


class TestExitCodes:
    def test_malformed_bundle_exits_2(self):
        bad_path = ROOT / "tests" / "_bad.json"
        bad_path.write_text("not json {")
        try:
            result = _run_cli([str(bad_path.relative_to(ROOT))], expect_exit=2)
            assert result.returncode == 2
        finally:
            bad_path.unlink(missing_ok=True)

    def test_usage_error_exits_4(self):
        result = _run_cli(["/nonexistent/path.json"], expect_exit=4)
        assert result.returncode == 4

    def test_jwks_directory_required_when_path_missing(self):
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_aal2.json",
            "--merchant-jwks", "/nonexistent/jwks",
        ], expect_exit=4)
        assert result.returncode == 4


class TestNoJWKS:
    def test_unverified_no_jwks_message(self):
        """Omitting --merchant-jwks reports 'unverified_no_jwks'."""
        result = _run_cli([
            "tests/GOLDEN/poai/bundle_aal2.json",
        ], expect_exit=0)
        assert result.returncode == 0
        assert "unverified_no_jwks" in result.stdout


class TestOfflineOperation:
    """The verifier MUST NOT make network calls."""

    def test_offline_uses_no_sockets(self):
        """Patch socket to detect any network attempts."""
        import openstore.verify.checks as checks_mod

        original_socket = socket.socket
        attempted = []

        def spy(*args, **kwargs):
            attempted.append((args, kwargs))
            return original_socket(*args, **kwargs)

        socket.socket = spy
        try:
            result = _run_cli([
                "tests/GOLDEN/poai/bundle_aal2.json",
                "--merchant-jwks", "tests/GOLDEN/poai/jwks/",
            ], expect_exit=0)
            assert result.returncode == 0
        finally:
            socket.socket = original_socket

        # The CLI process is a fresh subprocess; we can't intercept its sockets.
        # We test the in-process checks module instead.
        bundle = _load("bundle_aal2")
        ctx = checks_mod.VerifierContext(
            bundle=bundle,
            jwks_dir=GOLDEN / "jwks",
        )

        # Patch within this process
        attempted.clear()
        socket.socket = spy
        try:
            checks_mod.run_all_checks(ctx)
        finally:
            socket.socket = original_socket

        assert attempted == [], f"checks attempted network: {attempted}"
