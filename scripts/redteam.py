#!/usr/bin/env python3
# scripts/redteam.py
# Adversarial driver for the stage-10 red-team suite.
# Runs each named red-team scenario, then reports pass/fail per corpus.
# A failing scenario is a product bug (MUST NOT weaken the test) — the exit code
# is non-zero so CI fails the freeze.

from __future__ import annotations

import subprocess
import sys

SCENARIOS = {
    "inv1_cart_swap": "tests/redteam/test_inv1_cart_swap.py",
    "inv2_webauthn": "tests/redteam/test_inv2_webauthn.py",
    "inv3_idempotency": "tests/redteam/test_inv3_idempotency.py",
    "inv4_inv8_inv11": "tests/redteam/test_inv4_inv8_inv11.py",
    "inv5_ledger": "tests/redteam/test_inv5_ledger.py",
    "inv6_inv7_webhooks": "tests/redteam/test_inv6_inv7_webhooks.py",
    "inv9_oauth_alg": "tests/redteam/test_inv9_oauth_alg.py",
    "inv10_internal_routes": "tests/redteam/test_inv10_internal_routes.py",
    "inv12_audit": "tests/redteam/test_inv12_audit.py",
    "inv13_inv14_campaigns": "tests/redteam/test_inv13_inv14_campaigns.py",
    "r09_r010_no_bypass": "tests/redteam/test_r09_r010.py",
    "prompt_injection": "tests/redteam/test_prompt_injection.py",
}


def main() -> int:
    total_failures = 0
    # Deterministic order for reproducible output.
    for scenario, path in sorted(SCENARIOS.items()):
        proc = subprocess.run(["pytest", path, "-q"], capture_output=True, text=True)
        status = "PASS" if proc.returncode == 0 else "FAIL"
        print(f"[{status}] {scenario}")
        if proc.returncode != 0:
            total_failures += proc.returncode or 1
            print(proc.stdout[-1500:])
    if total_failures:
        print(f"\nRED-TEAM: {total_failures} failing scenario(s) — product bug, "
              f"surface via OPEN_QUESTIONS.md, do NOT weaken the test.")
        return 1
    print("\nRED-TEAM: all scenarios pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
