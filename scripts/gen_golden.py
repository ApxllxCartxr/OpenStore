"""Generate the §7 golden corpus into tests/fixtures/golden/.

Run: uv run python scripts/gen_golden.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.golden_mint import base_bundle, merchant_jwks, rechain  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "golden"
OUT.mkdir(parents=True, exist_ok=True)


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2) + "\n")
    print("wrote", OUT / name)


valid = base_bundle()
write("valid.json", valid)

# tampered: amount mismatch (chain re-signed so only the claim is wrong)
t_amount = base_bundle()
t_amount["transaction"]["amount_minor"] = 99999
rechain(t_amount)
write("tampered_amount.json", t_amount)

# tampered: unsupported compiler digest
t_digest = base_bundle()
t_digest["adjudication"]["compiler_digest"] = "sha256:deadbeef"
rechain(t_digest)
write("tampered_digest.json", t_digest)

# tampered: broken chain (sku edited, chain NOT recomputed)
t_chain = base_bundle()
t_chain["goods"]["items"][0]["sku"] = "TAMPERED"
write("tampered_chain.json", t_chain)

# merchant jwks for --merchant-jwks tests
write("merchant.jwks.json", merchant_jwks())
