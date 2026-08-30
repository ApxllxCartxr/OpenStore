import json
from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

from openstore.core import merkle
from openstore.models import ReceiptKind, TrustReceipt


class ReceiptRow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    receipt_id: str = Field(index=True, unique=True)
    kind: str
    ts: int = Field(index=True)
    actor_did: str
    envelope_json: str
    payload_ref: str
    merkle_proof_json: str = "null"
    daily_anchor_ref: Optional[str] = None
    statements_json: str = "[]"


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


class Ledger:
    def __init__(self, url: str = "sqlite:///:memory:"):
        # For in-memory SQLite, use StaticPool so all Session() calls
        # share the same underlying database.
        if url == "sqlite:///:memory:":
            from sqlalchemy.pool import StaticPool
            self.engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            self.engine = create_engine(url)
        SQLModel.metadata.create_all(self.engine)

    def append(self, receipt: TrustReceipt) -> None:
        with Session(self.engine) as s:
            row = ReceiptRow(
                receipt_id=receipt.receipt_id,
                kind=receipt.kind.value,
                ts=receipt.ts,
                actor_did=receipt.actor_did,
                envelope_json=json.dumps(receipt.envelope),
                payload_ref=receipt.payload_ref,
                merkle_proof_json=json.dumps(receipt.merkle_proof),
                daily_anchor_ref=receipt.daily_anchor_ref,
                statements_json=json.dumps(receipt.statements),
            )
            s.add(row)
            s.commit()

    def get(self, receipt_id: str) -> Optional[TrustReceipt]:
        with Session(self.engine) as s:
            row = s.exec(
                select(ReceiptRow).where(ReceiptRow.receipt_id == receipt_id)
            ).first()
            if row is None:
                return None
            return self._to_receipt(row)

    def by_kind(self, kind: ReceiptKind) -> List[TrustReceipt]:
        with Session(self.engine) as s:
            rows = s.exec(select(ReceiptRow).where(ReceiptRow.kind == kind.value)).all()
            return [self._to_receipt(r) for r in rows]

    def by_payload_ref(self, payload_ref: str) -> Optional[TrustReceipt]:
        with Session(self.engine) as s:
            row = s.exec(
                select(ReceiptRow).where(ReceiptRow.payload_ref == payload_ref)
            ).first()
            return self._to_receipt(row) if row is not None else None

    def day_receipts(self, date: str) -> List[ReceiptRow]:
        with Session(self.engine) as s:
            rows = s.exec(select(ReceiptRow)).all()
            return [r for r in rows if _day(r.ts) == date]

    def anchor_day(self, date: str, secret: bytes) -> tuple[bytes, bytes]:
        with Session(self.engine) as s:
            rows = s.exec(select(ReceiptRow)).all()
            rows = [r for r in rows if _day(r.ts) == date]
            ids = [r.receipt_id for r in rows]
            leaves = [merkle.merkle_leaf(i) for i in ids]
            root, proofs = merkle.build_tree(leaves)
            seed = merkle.daily_seed(date, secret)
            anchored = merkle.anchor(seed, root)
            for r in rows:
                proof = proofs.get(merkle.merkle_leaf(r.receipt_id).hex())
                r.merkle_proof_json = json.dumps(
                    [[side, h.hex()] for side, h in proof] if proof else []
                )
                r.daily_anchor_ref = anchored.hex()
            s.commit()
        return root, anchored

    def verify_receipt_inclusion(self, receipt_id: str, root: bytes) -> bool:
        with Session(self.engine) as s:
            row = s.exec(
                select(ReceiptRow).where(ReceiptRow.receipt_id == receipt_id)
            ).first()
            if row is None:
                return False
            proof = [(side, bytes.fromhex(h)) for side, h in json.loads(row.merkle_proof_json)]
            return merkle.verify_inclusion(receipt_id, root, proof)

    @staticmethod
    def _to_receipt(row: ReceiptRow) -> TrustReceipt:
        return TrustReceipt(
            receipt_id=row.receipt_id,
            kind=ReceiptKind(row.kind),
            ts=row.ts,
            actor_did=row.actor_did,
            envelope=json.loads(row.envelope_json),
            payload_ref=row.payload_ref,
            merkle_proof=json.loads(row.merkle_proof_json),
            daily_anchor_ref=row.daily_anchor_ref,
            statements=json.loads(row.statements_json),
        )
