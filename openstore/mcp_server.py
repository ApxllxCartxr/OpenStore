import asyncio

from mcp.server.mcpserver import MCPServer, Context

from openstore.gateway import FakeGateway, RazorpayGateway
from openstore.models import Mandate, Policy, Quote, QuoteItem
from openstore.runtime import MerchantRuntime, load_signing_key


def build_runtime(
    *,
    merchant_signing_key,
    legal_name: str = "OpenStore Merchant",
    country: str = "IN",
    per_txn_limit: int = 1_000_000_00,
    daily_limit: int = 5_000_000_00,
    catalog: dict = None,
    policy: Policy = None,
    gateway=None,
    ledger_url: str = "sqlite:///openstore_ledger.db",
    anchor_secret: bytes = b"change-me-anchor",
):
    if gateway is None:
        gateway = FakeGateway()
    return MerchantRuntime(
        merchant_signing_key=load_signing_key(merchant_signing_key) if isinstance(merchant_signing_key, str) else merchant_signing_key,
        legal_name=legal_name,
        country=country,
        per_txn_limit=per_txn_limit,
        daily_limit=daily_limit,
        catalog=catalog or {},
        policy=policy or Policy(spend_limit_paise=500_000_00),
        gateway=gateway,
        ledger_url=ledger_url,
        anchor_secret=anchor_secret,
    )


def build_mcp_server(runtime: MerchantRuntime, name: str = "openstore") -> MCPServer:
    mcp = MCPServer(name)
    rt = runtime

    @mcp.tool()
    def discover(ctx: Context) -> dict:
        """Return the merchant discovery document: DID, trust anchor policy,
        supported actions, and capabilities."""
        return rt.discover().model_dump()

    @mcp.tool()
    def create_mandate(mandate: dict, ctx: Context) -> dict:
        """Register a payer-signed mandate. `mandate` is a Mandate object with a
        JWS signed by the payer over the mandate fields."""
        m = Mandate.model_validate(mandate)
        receipt_id = rt.submit_mandate(m)
        return {"receipt_id": receipt_id, "mandate_id": m.mandate_id}

    @mcp.tool()
    def create_quote(items: list[dict], ctx: Context) -> dict:
        """Build a price-locked quote from catalog items. Prices are looked up
        server-side; the caller only supplies sku + quantity."""
        qitems = [QuoteItem.model_validate(i) for i in items]
        quote = rt.create_quote(qitems)
        return quote.model_dump()

    @mcp.tool()
    def create_order(quote: dict, mandate: dict, authority: dict = None, webauthn_assertion: dict = None, ctx: Context = None) -> dict:
        """Create and pay for an order from a quote and a previously registered
        mandate. The human-auth leg is a PoAI `authority` (from
        `webauthn_complete_assertion`) or a raw WebAuthn `webauthn_assertion`
        which the server verifies before building the bundle."""
        quote_obj = Quote.model_validate(quote)
        mandate_obj = Mandate.model_validate(mandate)
        return asyncio.run(rt.create_order(quote_obj, mandate_obj, authority=authority, webauthn_assertion=webauthn_assertion))

    @mcp.tool()
    def webauthn_register_begin(ctx: Context = None) -> dict:
        """Start WebAuthn credential registration. Returns a challenge for
        `navigator.credentials.create()`."""
        return rt.webauthn.begin_registration()

    @mcp.tool()
    def webauthn_register_complete(attestation_object: str, client_data_json: str, session_id: str, ctx: Context = None) -> dict:
        """Finish registration with the result of `navigator.credentials.create()`
        (fmt=none). Stores the credential so it can sign purchase assertions."""
        cred_id = rt.webauthn.register_credential(
            attestation_object=attestation_object, client_data_json=client_data_json, session_id=session_id
        )
        return {"credential_id": cred_id}

    @mcp.tool()
    def webauthn_begin_assertion(quote: dict, ctx: Context = None) -> dict:
        """Start a purchase-time WebAuthn ceremony. Returns a challenge + publicKey
        options for `navigator.credentials.get()`. The client must echo the quote
        back to `webauthn_complete_assertion` so the cart/policy binding matches."""
        qobj = Quote.model_validate(quote)
        oc = rt._order_context(qobj)
        return rt.webauthn.begin_assertion(oc["policy_dict"], oc["cart_hash"])

    @mcp.tool()
    def webauthn_complete_assertion(session_id: str, assertion: dict, quote: dict, ctx: Context = None) -> dict:
        """Finish the ceremony: verify the browser assertion and return the PoAI
        `authority` to pass into `create_order`."""
        qobj = Quote.model_validate(quote)
        oc = rt._order_context(qobj)
        return rt.webauthn.complete_assertion(
            session_id, assertion, policy=oc["policy_dict"], cart_hash=oc["cart_hash"]
        )

    @mcp.tool()
    def cancel_order(order_id: str, reason: str, ctx: Context) -> dict:
        """Cancel an order and release the hold / refund via the payment gateway."""
        return asyncio.run(rt.cancel_order(order_id, reason))

    @mcp.tool()
    def get_trust_receipt(receipt_id: str, ctx: Context) -> dict:
        """Fetch a trust receipt (signed envelope) by id."""
        r = rt.get_trust_receipt(receipt_id)
        if r is None:
            return {"error": "not found"}
        return r.model_dump()

    @mcp.tool()
    def get_daily_anchor(ctx: Context) -> dict:
        """Anchor today's receipts into a Merkle daily root and return root + anchor."""
        return rt.anchor_today()

    @mcp.tool()
    def raise_dispute(order_id: str, raised_by: str, reason: str, evidence_refs: list = None, ctx: Context = None) -> dict:
        """Raise a dispute against an order. Records a signed DISPUTE receipt."""
        return rt.raise_dispute(order_id, raised_by, reason, evidence_refs)

    @mcp.tool()
    def release_order(order_id: str, amount_paise: int, released_by: str, ctx: Context = None) -> dict:
        """Release held/escrowed funds for an order. Records a signed RELEASE receipt."""
        return rt.release_order(order_id, amount_paise, released_by)

    @mcp.tool()
    def return_order(order_id: str, items: list, reason: str, ctx: Context = None) -> dict:
        """Request a return of items for an order. Records a signed RETURN receipt."""
        return rt.return_order(order_id, items, reason)

    @mcp.tool()
    def refund_order(order_id: str, reason: str, ctx: Context = None) -> dict:
        """Refund a captured payment for an order. Records a signed REFUND receipt."""
        return asyncio.run(rt.refund_order(order_id, reason))

    return mcp
