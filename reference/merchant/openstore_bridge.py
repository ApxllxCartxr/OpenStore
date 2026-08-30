"""Bridge: build an openstore.MerchantRuntime backed by the reference merchant's
real config (signing key, catalog, payment credentials). This is the single point
where the OpenStore reference implementation *consumes* the generic openstore core,
so agents and the storefront share one trust system."""
import os

from reference.merchant.catalog.yaml_adapter import YAMLCatalogAdapter
from reference.merchant.config import settings

from openstore.gateway import FakeGateway, RazorpayGateway
from openstore.models import Policy
from openstore.mcp_server import build_runtime


def build_openstore_runtime():
    catalog = YAMLCatalogAdapter(settings.merchant_config_path)
    oc_catalog = {}
    for product in catalog.list_all():
        oc_catalog[product.sku] = {
            "title": product.name,
            "blocked": False,
            "unit_price_paise": product.price_minor,
        }

    gateway = FakeGateway()
    if os.environ.get("OPENSTORE_REAL_PAYMENTS") == "1" and settings.razorpay_key_id:
        gateway = RazorpayGateway(
            settings.razorpay_key_id,
            settings.razorpay_key_secret,
            settings.razorpay_webhook_secret,
        )

    return build_runtime(
        merchant_signing_key="merchant_signing_key.pem",
        legal_name=catalog.merchant_name,
        country="IN",
        per_txn_limit=1_000_000_00,
        daily_limit=5_000_000_00,
        catalog=oc_catalog,
        policy=Policy(spend_limit_paise=1_000_000_00),
        gateway=gateway,
        ledger_url="sqlite:///openstore_ledger.db",
        anchor_secret=(settings.merchant_id or "openstore").encode(),
    )
