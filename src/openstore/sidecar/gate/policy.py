"""Merchant-owned Policy, enforced by the Gate.

Edited in `/agentic`, enforced here, and never interpreted by an agent. Caps,
counts and quantities are evaluated **at the Product Group** (CONTEXT.md), so
buying two of each colour cannot walk through a two-per-order cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openstore.sidecar.core.codes import AuthorityKind, IntentMechanism, PaymentMethod


@dataclass(frozen=True)
class Policy:
    """§16.4's seeded values are the defaults; a Merchant edits them."""

    per_order_cap_minor: int = 2_500_000
    per_order_line_count: int = 10
    per_group_qty: int = 5
    per_group_qty_overrides: dict[str, int] = field(default_factory=lambda: {"plush": 2})
    blocked_tags: frozenset[str] = frozenset({"recalled"})
    enabled_methods: frozenset[PaymentMethod] = frozenset(
        {PaymentMethod.UPI, PaymentMethod.CASH_ON_DELIVERY}
    )
    enabled_authority_kinds: frozenset[AuthorityKind] = frozenset(
        {AuthorityKind.UPI_PIN, AuthorityKind.PASSKEY, AuthorityKind.CONFIRMED_INTENT}
    )
    enabled_intent_mechanisms: frozenset[IntentMechanism] = frozenset(
        {IntentMechanism.UPI_VERIFY, IntentMechanism.PASSKEY}
    )
    currency: str = "INR"
    window_open: bool = True
    """A Merchant can close the agent window entirely — closed refuses
    `window-closed` rather than pretending to be sold out."""

    def qty_cap_for(self, group_id: str) -> int:
        return self.per_group_qty_overrides.get(group_id, self.per_group_qty)


#: The one live Policy this process enforces.
#:
#: `Policy` is frozen, so an edit is a replacement rather than a mutation — and
#: a replacement is only visible to whoever reads *through* this holder. Every
#: surface that shows or applies a limit reads `current_policy()` at the moment
#: it needs one: the Gate when it decides, the console when it renders, the card
#: when it is fetched.
#:
#: They used to hold their own references instead. `ConsoleStore` and
#: `CheckoutContext` each built a `Policy()`, and `set_policy` swapped only the
#: console's, so a Merchant editing their cap changed the number on the screen
#: and nothing the Gate evaluated. `AgentSurface` was a third copy that nothing
#: ever set from a Policy at all — the card agreed with the Gate only because
#: three sets of defaults happened to match.
_live = Policy()


def current_policy() -> Policy:
    return _live


def set_current_policy(policy: Policy) -> None:
    """Replace the live Policy. Takes effect on the next read, with no restart
    and nothing to keep in step, because there is only one of these."""
    global _live
    _live = policy
