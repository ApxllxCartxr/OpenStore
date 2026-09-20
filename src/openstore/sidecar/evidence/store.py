"""Where sealed receipts live until the console and install phases give them a
real home.

Keyed by `receipt_id` — the unguessable 128-bit one, never by `order_id` and
never by the sequential `invoice_number`. A legal tax invoice and an IDOR-proof
lookup key need opposite properties (ADR-0020), and the store is where mixing
them up would actually bite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from openstore.sidecar.evidence.bundle import Bundle


@dataclass
class ReceiptStore:
    _by_id: dict[str, Bundle] = field(default_factory=dict)

    def put(self, bundle: Bundle) -> None:
        """Later versions replace earlier ones at the same id.

        v1 stays verifiable as a *document* — anyone holding its bytes can still
        check it — but the id resolves to the current version, because that is
        what the Consumer should see when they open the link again.
        """
        self._by_id[bundle.receipt_id] = bundle

    def get(self, receipt_id: str) -> Bundle | None:
        return self._by_id.get(receipt_id)

    def clear(self) -> None:
        self._by_id.clear()


@lru_cache(maxsize=1)
def get_receipt_store() -> ReceiptStore:
    return ReceiptStore()
