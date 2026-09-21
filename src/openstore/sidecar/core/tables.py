"""One `MetaData` for the sidecar's own database.

Every table the sidecar owns registers here — the Ledger, the Transcripts and
the evidence, and since durability landed, the in-flight state the money path
cannot lose across a restart: checkouts, the tokens that authorize them,
receipts, baskets and the refund queue.

It lives in `core` rather than beside any one of them because a shared
`MetaData` imported from a sibling makes that sibling look like the owner of
every other table, and because `create_all` needs exactly one.

**A table nobody imported is a table `create_all` does not create.** Declaring a
`Table` registers it as a side effect of import, so `core/db.py` imports every
owning module explicitly before it creates anything.
"""

from __future__ import annotations

from sqlalchemy import MetaData

metadata = MetaData()
