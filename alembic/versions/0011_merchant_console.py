"""Add merchant_sessions and merchant_settings tables (S24: merchant console).

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-16

Stage 24 introduced two tables that shipped without a forward migration: a
fresh DB picked them up from SQLModel.metadata.create_all in 0001, so tests
and clean installs were fine while any DB already at 0010 was left without
them. See openstore.models.MerchantSession / MerchantSetting.

Mirrors 0007_shopping_sessions.py's idempotent create pattern.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_SESSIONS = "merchant_sessions"
_SETTINGS = "merchant_settings"


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _SESSIONS not in existing:
        op.create_table(
            _SESSIONS,
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("operator_id", sa.String(length=64), nullable=False),
            sa.Column("credential_id", sa.String(length=256), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        )
        op.create_index("ix_merchant_sessions_token_hash", _SESSIONS, ["token_hash"], unique=True)
        op.create_index("ix_merchant_sessions_operator_id", _SESSIONS, ["operator_id"])
        op.create_index("ix_merchant_session_operator", _SESSIONS, ["operator_id"])

    if _SETTINGS not in existing:
        op.create_table(
            _SETTINGS,
            sa.Column("key", sa.String(length=128), primary_key=True),
            sa.Column("value", sa.String(length=4096), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _SETTINGS in existing:
        op.drop_table(_SETTINGS)

    if _SESSIONS in existing:
        op.drop_index("ix_merchant_session_operator", table_name=_SESSIONS)
        op.drop_index("ix_merchant_sessions_operator_id", table_name=_SESSIONS)
        op.drop_index("ix_merchant_sessions_token_hash", table_name=_SESSIONS)
        op.drop_table(_SESSIONS)
