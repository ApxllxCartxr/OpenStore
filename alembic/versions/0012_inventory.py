"""Add inventory tables (S26: real stock, no oversell).

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-16

inventory_items (per-SKU management state + platform-truth cache, unique on
merchant_id+sku) and inventory_ledger_entries (quantity movements as ledger
rows, unique idempotency_key) mirror core/ledger.py's discipline: movements
are rows, never in-place decrements. inventory_writebacks is the platform
write-back queue + DLQ (failed rows alert and retry).

Mirrors 0007_shopping_sessions.py's idempotent create pattern: a fresh DB
already gets these tables via SQLModel.metadata.create_all, so upgrade() is
a no-op there and real DDL on an existing DB stamped at 0011. See
openstore.models.InventoryItem / InventoryLedgerEntry / InventoryWriteback.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | None = None
depends_on: tuple[str, ...] | None = None

_ITEMS = "inventory_items"
_LEDGER = "inventory_ledger_entries"
_WRITEBACKS = "inventory_writebacks"


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _ITEMS not in existing:
        op.create_table(
            _ITEMS,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("merchant_id", sa.String(length=64), nullable=False),
            sa.Column("sku", sa.String(length=128), nullable=False),
            sa.Column("tracked", sa.Boolean(), nullable=False),
            sa.Column("low_stock_threshold", sa.Integer(), nullable=False),
            sa.Column("last_platform_qty", sa.Integer(), nullable=True),
            sa.Column("drifted", sa.Boolean(), nullable=False),
            sa.Column("low_stock_notified", sa.Boolean(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("merchant_id", "sku", name="ix_inventory_merchant_sku"),
        )
        op.create_index("ix_inventory_items_merchant_id", _ITEMS, ["merchant_id"])
        op.create_index("ix_inventory_items_sku", _ITEMS, ["sku"])

    if _LEDGER not in existing:
        op.create_table(
            _LEDGER,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("trace_id", sa.String(length=64), nullable=False),
            sa.Column("client_id", sa.String(length=64), nullable=False),
            sa.Column(
                "entry_type",
                sa.Enum(
                    "RESERVE",
                    "COMMIT",
                    "RELEASE",
                    "RESTOCK",
                    name="inventoryentrytype",
                ),
                nullable=False,
            ),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column("merchant_id", sa.String(length=64), nullable=False),
            sa.Column("sku", sa.String(length=128), nullable=False),
            sa.Column("reference_id", sa.String(length=64), nullable=False),
            sa.Column("account", sa.String(length=32), nullable=False),
            sa.Column("counterparty_account", sa.String(length=32), nullable=False),
            sa.Column("idempotency_key", sa.String(length=160), nullable=False, unique=True),
            sa.Column("description", sa.String(length=512), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_inventory_ledger_entries_trace_id", _LEDGER, ["trace_id"])
        op.create_index("ix_inventory_ledger_entries_client_id", _LEDGER, ["client_id"])
        op.create_index("ix_inventory_ledger_entries_merchant_id", _LEDGER, ["merchant_id"])
        op.create_index("ix_inventory_ledger_entries_sku", _LEDGER, ["sku"])
        op.create_index("ix_inventory_ledger_entries_reference_id", _LEDGER, ["reference_id"])
        op.create_index(
            "ix_inventory_ledger_entries_idempotency_key", _LEDGER, ["idempotency_key"], unique=True
        )
        op.create_index("ix_inventory_ledger_trace_client", _LEDGER, ["trace_id", "client_id"])
        op.create_index(
            "ix_inventory_ledger_merchant_sku", _LEDGER, ["merchant_id", "sku"]
        )
        op.create_index(
            "ix_inventory_ledger_reference_type", _LEDGER, ["reference_id", "entry_type"]
        )

    if _WRITEBACKS not in existing:
        op.create_table(
            _WRITEBACKS,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("merchant_id", sa.String(length=64), nullable=False),
            sa.Column("sku", sa.String(length=128), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False),
            sa.Column(
                "status",
                sa.Enum(
                    "PENDING",
                    "DELIVERED",
                    "FAILED",
                    "SKIPPED",
                    name="inventorywritebackstatus",
                ),
                nullable=False,
            ),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.String(length=1024), nullable=True),
            sa.Column("idempotency_key", sa.String(length=160), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_inventory_writebacks_merchant_id", _WRITEBACKS, ["merchant_id"])
        op.create_index("ix_inventory_writebacks_sku", _WRITEBACKS, ["sku"])
        op.create_index(
            "ix_inventory_writebacks_idempotency_key",
            _WRITEBACKS,
            ["idempotency_key"],
            unique=True,
        )
        op.create_index("ix_inventory_writeback_status", _WRITEBACKS, ["status"])
        op.create_index(
            "ix_inventory_writeback_merchant_sku", _WRITEBACKS, ["merchant_id", "sku"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if _WRITEBACKS in existing:
        op.drop_index("ix_inventory_writeback_merchant_sku", table_name=_WRITEBACKS)
        op.drop_index("ix_inventory_writeback_status", table_name=_WRITEBACKS)
        op.drop_index("ix_inventory_writebacks_idempotency_key", table_name=_WRITEBACKS)
        op.drop_index("ix_inventory_writebacks_sku", table_name=_WRITEBACKS)
        op.drop_index("ix_inventory_writebacks_merchant_id", table_name=_WRITEBACKS)
        op.drop_table(_WRITEBACKS)

    if _LEDGER in existing:
        op.drop_index("ix_inventory_ledger_reference_type", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_merchant_sku", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_trace_client", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_entries_idempotency_key", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_entries_reference_id", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_entries_sku", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_entries_merchant_id", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_entries_client_id", table_name=_LEDGER)
        op.drop_index("ix_inventory_ledger_entries_trace_id", table_name=_LEDGER)
        op.drop_table(_LEDGER)

    if _ITEMS in existing:
        op.drop_index("ix_inventory_items_sku", table_name=_ITEMS)
        op.drop_index("ix_inventory_items_merchant_id", table_name=_ITEMS)
        op.drop_table(_ITEMS)
