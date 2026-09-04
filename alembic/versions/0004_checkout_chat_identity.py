"""Add chat identity columns to checkouts (S11 Phase 3 / Q-018).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03

Checkout has no buyer chat identity, so nothing (webhook worker,
hold-release loop) knows which Discord user to DM when a payment lands, a
hold nears expiry, or a hold releases. Adds `chat_platform`, `chat_user_id`,
`chat_channel_id` — all nullable, since not every checkout is chat-originated
(API/test-created checkouts leave these unset) — mirroring the `handoffs`
table's same-named fields exactly (SPECS/stage-11-chat-flow.md, plan
go-with-the-push-functional-hearth.md, Q-018).

0001 uses SQLModel.metadata.create_all, so a fresh DB already gets these
columns via 0001 (openstore.models.Checkout now declares them) before this
revision ever runs. An existing DB migrated up to 0003 before these columns
existed does NOT have them yet, so this revision must add them there.
upgrade() checks column existence first so it is a no-op on a fresh DB and a
real DDL change on an existing one — never a collision either way.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_TABLE = "checkouts"
_COLUMNS = ("chat_platform", "chat_user_id", "chat_channel_id")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns(_TABLE)}

    if "chat_platform" not in existing_columns:
        op.add_column(_TABLE, sa.Column("chat_platform", sa.String(length=32), nullable=True))
    if "chat_user_id" not in existing_columns:
        op.add_column(_TABLE, sa.Column("chat_user_id", sa.String(length=64), nullable=True))
        op.create_index("ix_checkouts_chat_user_id", _TABLE, ["chat_user_id"])
    if "chat_channel_id" not in existing_columns:
        op.add_column(_TABLE, sa.Column("chat_channel_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {c["name"] for c in inspector.get_columns(_TABLE)}

    if "chat_channel_id" in existing_columns:
        op.drop_column(_TABLE, "chat_channel_id")
    if "chat_user_id" in existing_columns:
        op.drop_index("ix_checkouts_chat_user_id", table_name=_TABLE)
        op.drop_column(_TABLE, "chat_user_id")
    if "chat_platform" in existing_columns:
        op.drop_column(_TABLE, "chat_platform")
