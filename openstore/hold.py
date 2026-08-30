"""Hold & Cancel state machine (IMPLEMENTATION_SPEC §9.3).

When an order is placed, a hold is created keyed to its AAL tier. Higher AAL
(less assured) => longer hold. The buyer may cancel via a single-use, unauth
token (`/hold/{token}/cancel`); otherwise the hold auto-releases after its
window and the order settles.
"""

from __future__ import annotations

import secrets
import time
from typing import Callable, Optional

from sqlmodel import Session, select

from openstore.models import HoldRecordRow

# Pinned (plan.md): AAL 3 -> 0s, 2 -> 900s, 1 -> 3600s. AAL 0 = no hold.
HOLD_SECONDS = {3: 0, 2: 900, 1: 3600, 0: 0}


class HoldManager:
    def __init__(self, engine, on_cancel: Optional[Callable[[str], None]] = None):
        self.engine = engine
        self.on_cancel = on_cancel

    def create(self, order_id: str, aal_level: int) -> dict:
        token = secrets.token_urlsafe(32)
        hold_seconds = HOLD_SECONDS.get(aal_level, 3600)
        now = int(time.time())
        rec = HoldRecordRow(
            cancel_token=token,
            order_id=order_id,
            aal_level=aal_level,
            hold_seconds=hold_seconds,
            created_at=now,
            expires_at=now + hold_seconds,
        )
        with Session(self.engine) as s:
            s.add(rec)
            s.commit()
            token_out = rec.cancel_token
        return self._view(token_out)

    def get(self, token: str) -> Optional[dict]:
        with Session(self.engine) as s:
            rec = s.exec(select(HoldRecordRow).where(
                HoldRecordRow.cancel_token == token)).first()
            return self._rec_view(rec) if rec else None

    def cancel(self, token: str) -> dict:
        with Session(self.engine) as s:
            rec = s.exec(select(HoldRecordRow).where(
                HoldRecordRow.cancel_token == token)).first()
            if rec is None:
                raise ValueError("unknown hold token")
            if rec.status != "HELD":
                raise ValueError(f"hold already {rec.status}")
            rec.status = "CANCELLED"
            s.add(rec)
            s.commit()
            order_id = rec.order_id
        if self.on_cancel is not None:
            self.on_cancel(order_id)
        return {"order_id": order_id, "status": "CANCELLED"}

    def release_expired(self, now: Optional[int] = None) -> list[str]:
        now = now or int(time.time())
        released = []
        with Session(self.engine) as s:
            pending = s.exec(select(HoldRecordRow).where(
                HoldRecordRow.status == "HELD")).all()
            for rec in pending:
                if rec.expires_at <= now:
                    rec.status = "RELEASED"
                    s.add(rec)
                    released.append(rec.order_id)
            s.commit()
        return released

    def _rec_view(self, rec: HoldRecordRow) -> dict:
        return {
            "cancel_token": rec.cancel_token,
            "order_id": rec.order_id,
            "aal_level": rec.aal_level,
            "hold_seconds": rec.hold_seconds,
            "created_at": rec.created_at,
            "expires_at": rec.expires_at,
            "status": rec.status,
        }

    def _view(self, token: str) -> dict:
        v = self.get(token)
        return v or {}
