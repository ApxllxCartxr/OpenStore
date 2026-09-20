"""The closed Authority set, each kind declaring what it binds and when it lands.

ADR-0017. A bundle never reads "verified" unqualified: ranking the kinds would
have been the easier move and the wrong one — a standard admitting only the
strongest claim describes one rail, while one that makes every implementer
declare the strength of its own claim describes all of them.

**The kind declares when it lands, never the caller.** That is the whole reason
this table exists as data rather than as an `if` in the Gate: a caller that could
say "my authority landed early" could say it about `upi-pin`, and then a spend
would pass check 1 on an Authority that has not happened yet.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import StrEnum, unique

from openstore.sidecar.core.codes import AuthorityKind, BindingBy, BindingWhat, IntentMechanism


@unique
class HandleSource(StrEnum):
    """Where `consumer_id` was derived from.

    Recorded because one path has no payer handle at all: a COD order authorized
    by the `passkey` mechanism never touches a payment rail, so there is no VPA
    to derive from and the passkey credential ID is the source instead. Leaving
    this unstated produces a null `consumer_id` on exactly the path where
    attribution matters most, and a verifier that cannot tell which derivation
    was used cannot check either.
    """

    PAYER_HANDLE = "payer-handle"
    CREDENTIAL_ID = "credential-id"


@dataclass(frozen=True)
class KindSpec:
    kind: AuthorityKind
    mechanism: IntentMechanism | None
    binds: BindingWhat
    by: BindingBy
    lands_before_gate: bool
    live: bool = True


#: Normative, per §6.5's table.
SPECS: tuple[KindSpec, ...] = (
    KindSpec(
        AuthorityKind.UPI_PIN,
        None,
        BindingWhat.AMOUNT,
        BindingBy.PAYER_BANK,
        lands_before_gate=False,
    ),
    KindSpec(
        AuthorityKind.PASSKEY,
        None,
        BindingWhat.CART,
        BindingBy.PAYER_DEVICE,
        lands_before_gate=True,
    ),
    KindSpec(
        AuthorityKind.CONFIRMED_INTENT,
        IntentMechanism.UPI_VERIFY,
        BindingWhat.NONE,
        BindingBy.PAYER_BANK,
        lands_before_gate=True,
    ),
    KindSpec(
        AuthorityKind.CONFIRMED_INTENT,
        IntentMechanism.PASSKEY,
        BindingWhat.CART,
        BindingBy.PAYER_DEVICE,
        lands_before_gate=True,
    ),
    KindSpec(
        AuthorityKind.MANDATE,
        None,
        BindingWhat.NONE,
        BindingBy.MERCHANT,
        lands_before_gate=True,
        live=False,
    ),
)

_BY_KEY = {(s.kind, s.mechanism): s for s in SPECS}


def spec_for(kind: AuthorityKind, mechanism: IntentMechanism | None = None) -> KindSpec:
    """The spec for a kind, or a refusal.

    `mandate` has a spec and is `live=False`: it is **defined, registered and
    refused**, which is a different thing from absent. A reader of the registry
    can see that it was considered.
    """
    try:
        return _BY_KEY[(kind, mechanism)]
    except KeyError:
        raise ValueError(
            f"no Authority spec for {kind.value}"
            + (f"/{mechanism.value}" if mechanism else "")
            + (
                " — confirmed-intent needs a mechanism"
                if kind is AuthorityKind.CONFIRMED_INTENT
                else ""
            )
        ) from None


def is_live(kind: AuthorityKind, mechanism: IntentMechanism | None = None) -> bool:
    try:
        return spec_for(kind, mechanism).live
    except ValueError:
        return False


def derive_consumer_id(deploy_pseudonym_key: bytes, handle: str, *, source: HandleSource) -> str:
    """`HMAC(DEPLOY_PSEUDONYM_KEY, handle)` — per Merchant domain, never a
    cross-merchant identifier.

    The key is this deployment's own, so the same Consumer buying at two shops
    is two unrelated pseudonyms. That is the point: a stable cross-merchant id
    would make every sidecar a node in a tracking network nobody asked for.

    The plaintext handle lives only in the Merchant order row (ADR-0011) and
    reaches evidence only as this pseudonym, never as itself.
    """
    if not deploy_pseudonym_key:
        raise ValueError(
            "DEPLOY_PSEUDONYM_KEY is empty; a pseudonym keyed on nothing is a plaintext "
            "identifier with extra steps"
        )
    if not handle:
        raise ValueError(
            f"no {source.value} to derive a consumer_id from. A COD order authorized by the "
            f"passkey mechanism has no payer handle and must pass its credential id instead."
        )
    digest = hmac.new(
        deploy_pseudonym_key, f"{source.value}:{handle}".encode(), hashlib.sha256
    ).hexdigest()
    return f"csm_{digest[:32]}"
