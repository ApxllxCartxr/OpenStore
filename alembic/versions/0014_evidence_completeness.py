"""Persist the per-checkout assertion and ALLOW transcript (Q-050 / DECISION-051).

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-16

Every bundle the webhook path produced failed 5 of the offline verifier's 14
checks because two facts that existed at authorization time were dropped
before bundle time: the per-cart WebAuthn assertion (verified in
surfaces/studio.py, then discarded — only the sign counter survived) and
compile_decision's ALLOW transcript (audited on DENY, dropped on ALLOW).

Both land as nullable JSON on checkouts, following the Q-018/Q-020
nullable-column precedent. Nullable is load-bearing: pre-migration rows and
the agent/chat paths that hold no per-cart assertion keep producing exactly
the bundles they produce today. See openstore.models.Checkout.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_CHECKOUTS = "checkouts"
_COLUMNS = ("webauthn_assertion", "decision_transcript")


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_CHECKOUTS)}
    for name in _COLUMNS:
        if name not in existing:
            op.add_column(_CHECKOUTS, sa.Column(name, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns(_CHECKOUTS)}
    for name in _COLUMNS:
        if name in existing:
            op.drop_column(_CHECKOUTS, name)
