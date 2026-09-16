"""Add merchandising_rules table (S27: cross-sell and up-sell).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-16

merchandising_rules mirrors campaigns (kind CROSS_SELL | UPGRADE | BUNDLE,
the full DRAFT..REJECTED state machine, approval assertion fields, nullable
campaign_id through which BUNDLE discounts flow per DECISION-048).

Mirrors 0007_shopping_sessions.py's idempotent create pattern: a fresh DB
already gets this table via SQLModel.metadata.create_all, so upgrade() is a
no-op there and real DDL on an existing DB stamped at 0012. See
openstore.models.MerchandisingRule.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "merchandising_rules"


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _TABLE not in existing:
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("merchant_id", sa.String(length=64), nullable=False),
            sa.Column(
                "kind",
                sa.Enum(
                    "CROSS_SELL",
                    "UPGRADE",
                    "BUNDLE",
                    name="merchandisingkind",
                ),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=256), nullable=False),
            sa.Column("rationale", sa.String(length=2048), nullable=False),
            sa.Column("trigger_skus", sa.JSON(), nullable=False),
            sa.Column("suggested_sku", sa.String(length=128), nullable=False),
            sa.Column("campaign_id", sa.String(length=64), nullable=True),
            sa.Column("source_signals", sa.JSON(), nullable=False),
            sa.Column("draft_digest", sa.String(length=64), nullable=False),
            sa.Column(
                "state",
                sa.Enum(
                    "DRAFT",
                    "PENDING_APPROVAL",
                    "ACTIVE",
                    "PAUSED",
                    "EXPIRED",
                    "REJECTED",
                    # Reuses campaigns' PG enum type (the model column IS
                    # SQLEnum(CampaignState)): create_type=False, or CREATE
                    # TYPE would collide with the type 0001 already owns.
                    # Downgrade drops the table only — never this shared type.
                    name="campaignstate",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("approver_credential_id", sa.String(length=256), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("webauthn_assertion", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_merchandising_rules_merchant_id", _TABLE, ["merchant_id"])
        op.create_index("ix_merchandising_merchant_state", _TABLE, ["merchant_id", "state"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _TABLE not in existing:
        return
    op.drop_index("ix_merchandising_merchant_state", table_name=_TABLE)
    op.drop_index("ix_merchandising_rules_merchant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
