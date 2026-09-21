#!/usr/bin/env python3
"""Generate `tests/GOLDEN/card/` — the two documents a stranger actually reads.

The chat's card fetcher (C2) and the sidecar's card builder are written in
different languages and were never tested against each other: the chat's suite
built its own card fixtures, so it passed while agreeing with nothing. Every
real card was refused.

These files are the shared artifact that makes that impossible to repeat. The
sidecar's test asserts its routes still emit them; the chat's test feeds them
to the fetcher that has to read them. A change to either side that breaks the
other fails one of those two suites.

The demo's plain-http origin is the pinned case on purpose — it is the one
where an origin assumed to be `https://<domain>` produces a card whose every
endpoint is unreachable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openstore.sidecar.core.codes import AuthorityKind, PaymentMethod  # noqa: E402
from openstore.sidecar.protocols.wellknown import (  # noqa: E402
    agent_commerce_card,
    jwks_document,
    ucp_manifest,
)

DOMAIN = "spoiledduckie.localhost"
ORIGIN = f"http://{DOMAIN}"
OUT = Path(__file__).resolve().parent.parent / "tests" / "GOLDEN" / "card"

# A key set in the shape the keyring exports, pinned so the golden does not
# move every time a key is generated.
KEYRING_JWKS = {
    "keys": [
        {
            "kid": "sd-2026-09",
            "kty": "OKP",
            "crv": "Ed25519",
            "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
            "use": "sig",
        }
    ]
}

CARD = agent_commerce_card(
    merchant_domain=DOMAIN,
    merchant_name="SpoiledDuckie",
    origin=ORIGIN,
    enabled_methods=frozenset({PaymentMethod.UPI, PaymentMethod.CASH_ON_DELIVERY}),
    enabled_authority_kinds=frozenset(
        {AuthorityKind.UPI_PIN, AuthorityKind.PASSKEY, AuthorityKind.CONFIRMED_INTENT}
    ),
    demo=True,
)
MANIFEST = ucp_manifest(merchant_domain=DOMAIN, merchant_name="SpoiledDuckie", origin=ORIGIN)
JWKS = jwks_document(KEYRING_JWKS)


def main() -> None:
    assert CARD["endpoints"]["jwks"] == f"{ORIGIN}/.well-known/jwks.json", (
        "The card must name its key source on its own origin: the fetcher pins the keys "
        "it finds there, and a card that points elsewhere hands key custody away."
    )
    OUT.mkdir(parents=True, exist_ok=True)
    for name, document in (
        ("agent-commerce.json", CARD),
        ("ucp.json", MANIFEST),
        ("jwks.json", JWKS),
    ):
        (OUT / name).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
