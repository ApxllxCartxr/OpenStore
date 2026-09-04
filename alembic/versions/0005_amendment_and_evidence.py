"""Add amendment draft + evidence bundle columns (S11 Phase 4 / Q-020).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-03

Phase 4 (negotiation, amendment, evidence) needs two more nullable columns
than the Phase 0-3 schema has:
  - handoffs.amendment_draft (JSON): the drafted amendment
    (MerchantAgent.draft_amendment output) plus the cart it was drafted
    against, so it survives the round-trip from drafting to approval.
  - checkouts.request_text (String): the original chat goal, so a completed
    purchase's evidence bundle can populate PoAI human_intent (e8).
  - checkouts.poai_bundle (JSON): the produced evidence bundle, persisted so
    /orders/{checkout_id}/evidence can serve it back.

All three are nullable (mirrors 0004: not every checkout/handoff is
chat-originated, and no checkout has a bundle until RELEASED).

0001 uses SQLModel.metadata.create_all, so a fresh DB already gets these
columns via 0001 (openstore.models.Handoff/Checkout now declare them) before
this revision ever runs. An existing DB migrated up to 0004 before these
columns existed does NOT have them yet, so this revision must add them
there. upgrade() checks column existence first so it is a no-op on a fresh
DB and a real DDL change on an existing one — never a collision either way.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_HANDOFFS = "handoffs"
_CHECKOUTS = "checkouts"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    handoff_columns = {c["name"] for c in inspector.get_columns(_HANDOFFS)}
    if "amendment_draft" not in handoff_columns:
        op.add_column(_HANDOFFS, sa.Column("amendment_draft", sa.JSON(), nullable=True))

    checkout_columns = {c["name"] for c in inspector.get_columns(_CHECKOUTS)}
    if "request_text" not in checkout_columns:
        op.add_column(_CHECKOUTS, sa.Column("request_text", sa.String(length=4096), nullable=True))
    if "poai_bundle" not in checkout_columns:
        op.add_column(_CHECKOUTS, sa.Column("poai_bundle", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    checkout_columns = {c["name"] for c in inspector.get_columns(_CHECKOUTS)}
    if "poai_bundle" in checkout_columns:
        op.drop_column(_CHECKOUTS, "poai_bundle")
    if "request_text" in checkout_columns:
        op.drop_column(_CHECKOUTS, "request_text")

    handoff_columns = {c["name"] for c in inspector.get_columns(_HANDOFFS)}
    if "amendment_draft" in handoff_columns:
        op.drop_column(_HANDOFFS, "amendment_draft")
