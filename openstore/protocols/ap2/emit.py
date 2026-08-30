"""AP2 emission (INTEROP_SPEC §6.3b).

Render an OpenStore authority/bundle as an AP2-shaped object so a bundle can be
consumed by an AP2-native party. Emission is lossy in the other direction and
MUST be labelled: `emit_authority` returns `(ap2_object, dropped_fields)`.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple


def emit_authority(bundle: Mapping[str, Any]) -> Tuple[dict, Tuple[str, ...]]:
    """Convert a PoAI bundle's authority section into an AP2-shaped object.

    Returns (ap2_object, dropped_fields). `dropped_fields` lists core fields AP2
    cannot represent, and the caller MUST surface them (R6.3b).
    """
    authority = bundle.get("authority", {})
    aal = bundle.get("aal", {})

    ap2_object: dict[str, Any] = {
        "vct": "mandate.checkout.closed.1",
        "type": "cart_mandate" if authority.get("scheme") == "ap2_cart_mandate" else "intent_mandate",
        "scheme": authority.get("scheme"),
        "policy": authority.get("policy"),
        "policy_hash": authority.get("policy_hash"),
    }

    # These core fields have no AP2 equivalent and are dropped — but reported.
    dropped: list[str] = []
    if authority.get("webauthn") is not None:
        dropped.append("authority.webauthn")
    if authority.get("presentation") is not None:
        dropped.append("authority.presentation")
    if bundle.get("poai_version") is not None:
        dropped.append("poai_version")
    if aal.get("predicates") is not None:
        dropped.append("aal.predicates")

    return ap2_object, tuple(dropped)
