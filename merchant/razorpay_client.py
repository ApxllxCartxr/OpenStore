from merchant.config import settings

_client = None
INJECT_TIMEOUT = {"enabled": False}


def _get_client():
    global _client
    if _client is None:
        import razorpay
        _client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    return _client


def create_order_and_payment_link(amount_minor: int, checkout_id: str) -> dict:
    client = _get_client()
    order = client.order.create({
        "amount": amount_minor,
        "currency": "INR",
        "receipt": checkout_id,
    })
    link = client.payment_link.create({
        "amount": amount_minor,
        "currency": "INR",
        "reference_id": checkout_id,
        "notes": {"order_id": order["id"]},
    })
    return {
        "razorpay_order_id": order["id"],
        "payment_link_id": link["id"],
        "payment_link_url": link["short_url"],
    }
