"""Where sealed receipts live.

Keyed by `receipt_id` — the unguessable 128-bit one, never by `order_id` and
never by the sequential `invoice_number`. A legal tax invoice and an IDOR-proof
lookup key need opposite properties (ADR-0020), and the store is where mixing
them up would actually bite.

**Durable, because a receipt link outlives the process that sealed it.** This
held a dict until 09-21, which meant every restart voided every receipt URL
already handed to a Consumer: the evidence is the product, and evidence that
disappears on deploy is not evidence. The row stores the sealed document as the
canonical JSON that was signed, so what comes back out verifies — a re-derived
bundle would be a *second* document that happens to look like the first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Column as Col
from sqlalchemy import DateTime, Integer, String, Table, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openstore.sidecar.core.db import session_scope
from openstore.sidecar.core.tables import metadata
from openstore.sidecar.evidence.bundle import Bundle

#: One row per receipt id, replaced in place when a later version is sealed.
receipts = Table(
    "receipts",
    metadata,
    Col("receipt_id", String(64), primary_key=True),
    Col("version", Integer, nullable=False),
    Col("merchant_domain", String(255), nullable=False),
    Col("sealed_at", String(32), nullable=False),
    Col("stored_at", DateTime(timezone=True), nullable=False),
    # The signed document verbatim. `Text` rather than a JSON column on
    # purpose: a JSON column is free to re-order keys, and these bytes are what
    # the signature covers.
    Col("document", Text, nullable=False),
)


def _to_bundle(document: str) -> Bundle:
    # Imported here rather than at module scope: `verify.checks` imports the
    # evidence package, and a top-level import would close the cycle.
    from openstore.sidecar.verify.checks import bundle_from_dict

    return bundle_from_dict(json.loads(document))


@dataclass
class ReceiptStore:
    """Every method takes its own transaction. Receipts are written once at the
    end of a purchase and read by strangers opening a link; neither belongs
    inside somebody else's unit of work."""

    sessionmaker: async_sessionmaker[AsyncSession] | None = None

    def _maker(self) -> async_sessionmaker[AsyncSession]:
        if self.sessionmaker is None:
            raise RuntimeError(
                "This ReceiptStore has no database, so nothing can be sealed or read "
                "back. Set SIDECAR_DATABASE_URL."
            )
        return self.sessionmaker

    async def put(self, bundle: Bundle) -> None:
        """Later versions replace earlier ones at the same id.

        v1 stays verifiable as a *document* — anyone holding its bytes can still
        check it — but the id resolves to the current version, because that is
        what the Consumer should see when they open the link again.
        """
        document = json.dumps(bundle.to_dict(), sort_keys=True, separators=(",", ":"))
        values = {
            "receipt_id": bundle.receipt_id,
            "version": bundle.version,
            "merchant_domain": bundle.merchant_domain,
            "sealed_at": bundle.sealed_at,
            "stored_at": datetime.now(UTC),
            "document": document,
        }
        async with session_scope(self._maker()) as session:
            existing = (
                await session.execute(
                    select(receipts.c.receipt_id).where(receipts.c.receipt_id == bundle.receipt_id)
                )
            ).first()
            if existing is None:
                await session.execute(receipts.insert().values(**values))
            else:
                await session.execute(
                    receipts.update()
                    .where(receipts.c.receipt_id == bundle.receipt_id)
                    .values(**values)
                )

    async def get(self, receipt_id: str) -> Bundle | None:
        async with session_scope(self._maker()) as session:
            row = (
                await session.execute(
                    select(receipts.c.document).where(receipts.c.receipt_id == receipt_id)
                )
            ).first()
        return _to_bundle(row.document) if row is not None else None

    async def all(self) -> list[Bundle]:
        """Every sealed receipt, oldest first — the console's board reads this."""
        async with session_scope(self._maker()) as session:
            rows = (
                await session.execute(
                    select(receipts.c.document).order_by(
                        receipts.c.stored_at, receipts.c.receipt_id
                    )
                )
            ).all()
        return [_to_bundle(r.document) for r in rows]

    async def clear(self) -> None:
        async with session_scope(self._maker()) as session:
            await session.execute(receipts.delete())


_store = ReceiptStore()


def configure_receipts(sessionmaker: async_sessionmaker[AsyncSession] | None) -> None:
    """Point the process-wide store at this deploy's database.

    Configured at startup like every other seam rather than resolved per call,
    so a store that reached no database is a boot-time fact and not a surprise
    at the end of somebody's purchase.
    """
    _store.sessionmaker = sessionmaker


def get_receipt_store() -> ReceiptStore:
    return _store
