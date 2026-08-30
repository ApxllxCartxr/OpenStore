"""Start the OpenStore merchant server.

Usage:
    uv run python run.py [--port 8000] [--password changeme]
"""

import argparse
import os
import sys
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from openstore.config import settings


def main():
    parser = argparse.ArgumentParser(description="Start OpenStore merchant server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--password", default=os.environ.get("OPENSTORE_OPERATOR_PASSWORD", "changeme"))
    args = parser.parse_args()

    # Generate or load merchant signing key
    key_path = "merchant_signing_key.pem"
    if os.path.exists(key_path):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        key = load_pem_private_key(open(key_path, "rb").read(), password=None)
        print(f"Loaded merchant key from {key_path}")
    else:
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.PEM,
            format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.PKCS8,
            encryption_algorithm=__import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption(),
        )
        with open(key_path, "wb") as f:
            f.write(raw)
        print(f"Generated new merchant key → {key_path}")

    from openstore.gateway import FakeGateway
    from openstore.runtime import MerchantRuntime
    from openstore.server import create_http_app

    # Demo catalog — gelateria
    catalog = {
        "GEL-VAN-500": {"title": "Vegan Gelato (500ml)", "unit_price_paise": 21000, "tags": ["vegan", "dairy-free"]},
        "GEL-CHOC-500": {"title": "Chocolate Gelato (500ml)", "unit_price_paise": 23000, "tags": ["chocolate"]},
        "GEL-PIS-500": {"title": "Pistachio Gelato (500ml)", "unit_price_paise": 26000, "tags": ["nuts"]},
        "GEL-RUM-500": {"title": "Rum Raisin Gelato (500ml)", "unit_price_paise": 26000, "tags": ["alcohol", "dessert"]},
        "GEL-STRW-300": {"title": "Strawberry Gelato (300ml)", "unit_price_paise": 15000, "tags": ["fruit"]},
        "GEL-MANGO-300": {"title": "Mango Gelato (300ml)", "unit_price_paise": 15000, "tags": ["fruit"]},
        "CON-VAN": {"title": "Vanilla Cone", "unit_price_paise": 8000, "tags": ["cone"]},
        "CON-CHOC": {"title": "Chocolate Cone", "unit_price_paise": 8000, "tags": ["cone", "chocolate"]},
        "AFF-VAN": {"title": "Affogato (Vanilla)", "unit_price_paise": 18000, "tags": ["coffee", "dairy-free"]},
        "AFF-CHOC": {"title": "Affogato (Chocolate)", "unit_price_paise": 18000, "tags": ["coffee", "chocolate"]},
        "SUNDae-500": {"title": "Classic Sundae", "unit_price_paise": 32000, "tags": ["dessert", "nuts"]},
        "BAN-SPLIT": {"title": "Banana Split", "unit_price_paise": 35000, "tags": ["dessert", "fruit"]},
    }

    # Merchant policy
    policy = MagicMock()
    policy.blocked_skus = []
    policy.spend_limit_paise = 100000  # ₹1,000

    runtime = MerchantRuntime(
        merchant_signing_key=key,
        legal_name="Gelateria Roma",
        country="IN",
        per_txn_limit=100000,
        daily_limit=1000000,
        catalog=catalog,
        policy=policy,
        gateway=FakeGateway(),
        ledger_url=settings.ledger_url,
    )

    app = create_http_app(runtime, operator_password=args.password)

    import uvicorn
    import sys
    # Print banner before uvicorn takes over stdout
    banner = (
        f"\n🍦 OpenStore Gelateria Roma — http://{args.host}:{args.port}\n"
        f"   Storefront:    http://localhost:{args.port}/\n"
        f"   Policy Studio: http://localhost:{args.port}/intent/studio\n"
        f"   Agent Console: http://localhost:{args.port}/admin/agents\n"
        f"   Agent Policy:  http://localhost:{args.port}/.well-known/agent-policy.json\n"
        f"   WebAuthn HTML: http://localhost:{args.port}/webauthn.html\n"
        f"   Merchant DID:  {runtime.did}\n"
        f"   Operator pass: {args.password}\n"
    )
    sys.stderr.write(banner)
    sys.stderr.flush()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
