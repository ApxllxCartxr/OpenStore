#!/usr/bin/env python3
"""Pin the live compiler digest into verify/checks.py KNOWN_COMPILER_DIGESTS.

Usage: uv run python scripts/pin_compiler_digest.py [--check]

--check exits 1 when the live digest is absent from the pinned set (CI gate).
Without flags it prints the live digest and the file/line to update.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ROOT / "src" / "openstore" / "verify" / "checks.py"


def live_digest() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from openstore.core.compiler import get_compiler_digest

    return get_compiler_digest()


def main() -> int:
    digest = live_digest()
    text = CHECKS.read_text(encoding="utf-8")
    if digest in text:
        print(f"pinned: {digest}")
        return 0
    print(f"LIVE DIGEST NOT PINNED: {digest}", file=sys.stderr)
    print(f"Add it to KNOWN_COMPILER_DIGESTS in {CHECKS}", file=sys.stderr)
    known = re.findall(r'"sha256:[0-9a-f]{64}"', text)
    print("currently pinned:", file=sys.stderr)
    for k in known:
        print(f"  {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
