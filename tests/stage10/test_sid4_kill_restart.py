# tests/stage10/test_sid4_kill_restart.py
# SID-4 — Crash/restart recovery semantics.
#
# A merchant serve process is killed mid-flight (after the Razorpay payment link
# was created but before the buyer paid). On restart the sidecar must:
#   - not leave an orphaned non-terminal intent (HELD checkout with no recovery
#     path for its payment link), and
#   - never create a duplicate Razorpay object for the same checkout_id.
#
# The crash is simulated as two SUBPROCESSES against the same on-disk DB: the
# first seeds a HELD checkout with a persisted payment link (the process then
# "dies" — it does not confirm the payment). The second (restart) re-invokes the
# payment-link create path with a Razorpay mock that returns the duplicate-
# reference_id error exactly as the live PSP would. The driver must recover by
# reference_id (INV-4) and adopt the single existing link.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SUBPROCESS = ROOT / "tests" / "stage10" / "_merchant_run.py"


def _run(db_path: Path, merchant_id: str, op: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(_SUBPROCESS), str(db_path), merchant_id, op],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"SID-4 subprocess failed ({op}): {proc.stderr}"
    return proc.stdout.strip()


class TestKillRestartRecovery:
    def test_no_orphaned_intent_and_no_duplicate_razorpay_object(self, tmp_path: Path):
        db = tmp_path / "merchant.db"

        # Process #1: create a HELD checkout whose Razorpay link was persisted,
        # then the process is killed (op returns, no confirm/payment follows).
        ckid = _run(db, "gelateria-milano", "seed_pending_link")
        assert ckid == "chk_gelateria"

        # Process #2 (restart): re-run the payment-link creation. The mock PSP
        # already holds the link; the driver must DEDUPE, not duplicate.
        out = _run(db, "gelateria-milano", "recover_no_duplicate")
        assert "recovered" in out, out
        # Recovered onto the same (single) link id from the pre-crash process.
        assert out.endswith("plink_chk_gelateria"), out
