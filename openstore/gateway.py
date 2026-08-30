from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class PaymentResult:
    payment_id: str
    status: str
    reference_id: str


class PaymentGateway(ABC):
    @abstractmethod
    async def create_payment(self, amount_paise: int, currency: str, reference_id: str, notes: dict) -> PaymentResult: ...

    @abstractmethod
    async def cancel_payment(self, payment_id: str, amount_paise: int) -> PaymentResult: ...

    @abstractmethod
    async def refund_payment(self, payment_id: str, amount_paise: int) -> PaymentResult: ...

    @abstractmethod
    async def fetch_payment_status(self, reference_id: str) -> Optional[str]: ...


class FakeGateway(PaymentGateway):
    def __init__(self):
        self.calls = []
        self._status_map: Dict[str, str] = {}  # reference_id -> status

    async def create_payment(self, amount_paise, currency, reference_id, notes):
        self.calls.append(("create", reference_id, amount_paise))
        self._status_map[reference_id] = "captured"
        return PaymentResult(payment_id=f"pay_{reference_id}", status="captured", reference_id=reference_id)

    async def cancel_payment(self, payment_id, amount_paise):
        self.calls.append(("cancel", payment_id, amount_paise))
        return PaymentResult(payment_id=payment_id, status="refunded", reference_id=payment_id)

    async def refund_payment(self, payment_id, amount_paise):
        self.calls.append(("refund", payment_id, amount_paise))
        return PaymentResult(payment_id=payment_id, status="refunded", reference_id=payment_id)

    async def fetch_payment_status(self, reference_id: str) -> Optional[str]:
        return self._status_map.get(reference_id)


class RazorpayGateway(PaymentGateway):
    async def fetch_payment_status(self, reference_id: str) -> Optional[str]:
        try:
            resp = self.client.payment_link.fetch(reference_id)
            return resp.get("status")
        except Exception:
            return None
    def __init__(self, key_id: str, key_secret: str, webhook_secret: str = ""):
        import razorpay

        self.client = razorpay.Client(auth=(key_id, key_secret))
        self.webhook_secret = webhook_secret

    async def create_payment(self, amount_paise, currency, reference_id, notes):
        resp = self.client.payment_link.create(
            {
                "amount": amount_paise,
                "currency": currency,
                "reference_id": reference_id,
                "notes": notes,
                "callback_url": notes.get("_callback_url", ""),
            }
        )
        return PaymentResult(
            payment_id=resp["id"],
            status=resp.get("status", "created"),
            reference_id=reference_id,
        )

    async def cancel_payment(self, payment_id, amount_paise):
        # Razorpay payment *links* are pay-later: nothing is charged until the
        # buyer pays, so a link may not be cancellable. Treat cancel as best-effort;
        # the authoritative release is the ledger CANCEL receipt. Refunds after a
        # real capture use refund_payment (payments/refund endpoint).
        try:
            resp = self.client.payment_link.cancel(payment_id)
            return PaymentResult(
                payment_id=payment_id,
                status=resp.get("status", "cancelled"),
                reference_id=payment_id,
            )
        except Exception:
            return PaymentResult(
                payment_id=payment_id,
                status="cancel_skipped",
                reference_id=payment_id,
            )

    async def refund_payment(self, payment_id, amount_paise):
        resp = self.client.payment.refund(payment_id, amount_paise)
        return PaymentResult(
            payment_id=payment_id,
            status=resp.get("status", "refunded"),
            reference_id=payment_id,
        )
