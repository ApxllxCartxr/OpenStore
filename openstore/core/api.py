"""Protocol-free commerce core (INTEROP_SPEC §2).

This is the only place money decisions are made. Adapters translate a protocol
request into the frozen types here and call these methods; they never touch the
catalog prices, the compiler, or the ledger directly (R1.1/R1.2). The core is
permitted to delegate persistence and payments to a `MerchantRuntime`, but all
*decisions* — price derivation, the compiler verdict, the AAL — happen here, so
they are identical regardless of which protocol called.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from openstore.aal import Predicates, resolve_aal
from openstore.canonical import canonical_json_bytes, digest
from openstore.compiler import (
    CompilerContext,
    CompilerItem,
    CompilerPolicy,
    CompilerVerdict,
    LegacyPolicyError,
    compile_decision,
)
from openstore.core.authority import cap_aal, verify
from openstore.core.types import (
    Actor,
    AuthorityPresentation,
    CartView,
    CheckoutView,
    ConfirmResult,
    DeliveryAddress,
    HoldView,
    LineItemRequest,
    OrderView,
    ProductView,
    SearchQuery,
)

_ID = 0


def _new_id(prefix: str) -> str:
    global _ID
    _ID += 1
    return f"{prefix}{_ID}"


class CommerceCore:
    def __init__(self, runtime):
        self.rt = runtime
        self._carts: dict[int, tuple[LineItemRequest, ...]] = {}
        self._checkouts: dict[str, CheckoutView] = {}
        self._orders: dict[str, dict] = {}
        self._bundles: dict[str, dict] = {}
        self._spend: dict[str, int] = {}          # Actor.subject -> spent_minor
        self._txcount: dict[str, int] = {}        # Actor.subject -> completed count
        self._idempotency: dict[str, str] = {}

    # -- catalog ---------------------------------------------------------
    def _product(self, sku: str) -> dict:
        prod = self.rt.catalog.get(sku)
        if prod is None:
            raise ValueError(f"unknown sku {sku}")
        return prod

    def _price_of(self, sku: str) -> int:
        # R2.1a — price is ALWAYS re-derived from the catalog, never trusted from a caller.
        return int(self._product(sku).get("unit_price_paise", 0))

    def _tags_of(self, sku: str) -> tuple[str, ...]:
        return tuple(self._product(sku).get("tags", []))

    def search_products(self, query: SearchQuery) -> list[ProductView]:
        out = []
        for sku, prod in self.rt.catalog.items():
            if query.query and query.query.lower() not in (sku + " " + str(prod.get("title", ""))).lower():
                continue
            out.append(ProductView(
                sku=sku,
                title=str(prod.get("title", sku)),
                unit_price_paise=self._price_of(sku),
                tags=self._tags_of(sku),
                available=not prod.get("blocked", False),
            ))
            if len(out) >= query.limit:
                break
        return out

    def get_product(self, sku: str) -> ProductView:
        prod = self._product(sku)
        return ProductView(
            sku=sku,
            title=str(prod.get("title", sku)),
            unit_price_paise=self._price_of(sku),
            tags=self._tags_of(sku),
            available=not prod.get("blocked", False),
        )

    # -- cart ------------------------------------------------------------
    def _validate_items(self, items: tuple[LineItemRequest, ...]) -> tuple[LineItemRequest, ...]:
        # Drop any price a caller may have smuggled in: LineItemRequest has no price field,
        # so re-build from sku+qty only (R2.1a).
        clean = []
        for it in items:
            self._product(it.sku)  # raises on unknown sku
            clean.append(LineItemRequest(sku=it.sku, qty=int(it.qty)))
        return tuple(clean)

    def _total(self, items: tuple[LineItemRequest, ...]) -> int:
        return sum(self._price_of(i.sku) * i.qty for i in items)

    def create_cart(self, actor: Actor, items: tuple[LineItemRequest, ...]) -> CartView:
        items = self._validate_items(items)
        cart_id = len(self._carts) + 1
        self._carts[cart_id] = items
        return CartView(cart_id=cart_id, items=items, total_minor=self._total(items))

    def update_cart(self, actor: Actor, cart_id: int, items: tuple[LineItemRequest, ...]) -> CartView:
        if cart_id not in self._carts:
            raise ValueError(f"unknown cart {cart_id}")
        items = self._validate_items(items)
        self._carts[cart_id] = items
        return CartView(cart_id=cart_id, items=items, total_minor=self._total(items))

    # -- checkout --------------------------------------------------------
    def _compiler_policy(self) -> CompilerPolicy:
        d = dict(self.rt.compiler_policy_dict())
        return CompilerPolicy(**d)

    def initiate_checkout(self, actor: Actor, cart_id: int,
                          delivery: DeliveryAddress) -> CheckoutView:
        if cart_id not in self._carts:
            raise ValueError(f"unknown cart {cart_id}")
        items = self._carts[cart_id]
        total = self._total(items)
        # §9.5b — tell the agent the tier requirement before confirmation.
        # Baseline tier the merchant requires; the confirming authority may raise it.
        required_aal = 1
        checkout_id = _new_id("C")
        # PRODUCTION_READINESS §0.3 — 5-minute checkout expiry
        import time as _time
        expires_at = int(_time.time()) + 300
        view = CheckoutView(
            checkout_id=checkout_id, cart_id=cart_id, total_minor=total,
            required_aal=int(required_aal), delivery=delivery,
            expires_at_unix=expires_at,
        )
        self._checkouts[checkout_id] = view
        return view

    def remaining_budget(self, subject: str) -> int:
        cap = self._compiler_policy().max_spend_total_minor
        return cap - self._spend.get(subject, 0)

    # -- confirm ---------------------------------------------------------
    def confirm_checkout(self, actor: Actor, checkout_id: str,
                         authority: AuthorityPresentation,
                         idempotency_key: str) -> ConfirmResult:
        if checkout_id not in self._checkouts:
            raise ValueError(f"unknown checkout {checkout_id}")
        if idempotency_key in self._idempotency:
            prev = self._idempotency[idempotency_key]
            return self._orders[prev]["confirm"]
        view = self._checkouts[checkout_id]
        # PRODUCTION_READINESS §0.3 — enforce checkout expiry
        if view.expires_at_unix > 0:
            import time as _time
            if _time.time() > view.expires_at_unix:
                raise ValueError("checkout_expired")
        items = self._carts[view.cart_id]

        citems = tuple(CompilerItem(
            sku=i.sku, qty=i.qty,
            unit_minor=self._price_of(i.sku), tags=self._tags_of(i.sku),
        ) for i in items)
        policy = self._compiler_policy()
        ctx = CompilerContext(
            merchant_id=policy.merchant_id,
            currency=policy.currency,
            evaluated_at_unix=int(time.time()),
            spent_minor=self._spend.get(actor.subject, 0),
            transactions_count=self._txcount.get(actor.subject, 0),
        )
        try:
            verdict: CompilerVerdict = compile_decision(citems, policy, ctx)
        except LegacyPolicyError:
            raise

        if verdict.verdict != "ALLOW":
            raise ValueError(f"compiler_denied: {verdict.reason_code}")

        # Resolve AAL: predicates from the authority scheme, then cap by scheme.
        verified = verify(authority)
        preds = Predicates(**{k: bool(v) for k, v in verified.predicates.items()})
        level, reasons = resolve_aal(preds, policy.policy_version)
        level, reasons = cap_aal(level, reasons, authority.scheme)

        # Record spend + tx count (keyed by Actor.subject so budget is shared
        # across protocols, R5.3b).
        self._spend[actor.subject] = self._spend.get(actor.subject, 0) + view.total_minor
        self._txcount[actor.subject] = self._txcount.get(actor.subject, 0) + 1

        order_id = _new_id("O")
        bundle = self._build_bundle(checkout_id, order_id, verdict, level, reasons, authority, verified)
        hold = HoldView(
            hold_id=_new_id("H"), order_id=order_id, level=level,
            status="HELD" if level < 3 else "OPEN", expires_at_unix=int(time.time()) + 3600,
        )
        status = "HELD" if level < 3 else "ORDER_CREATED"
        result = ConfirmResult(
            checkout_id=checkout_id, status=status,
            payment_link_url=f"/pay/{order_id}", aal_level=level,
            hold=hold, bundle_id=bundle["bundle_id"],
        )
        order = {
            "order_id": order_id, "checkout_id": checkout_id, "status": status,
            "total_minor": view.total_minor, "aal_level": level,
            "confirm": result, "bundle": bundle,
        }
        self._orders[order_id] = order
        self._bundles[bundle["bundle_id"]] = bundle
        self._idempotency[idempotency_key] = order_id
        return result

    def _build_bundle(self, checkout_id, order_id, verdict, level, reasons,
                      authority, verified) -> dict:
        native = authority.scheme == "native_webauthn"
        authority_section = {
            "scheme": authority.scheme,
            "policy": authority.policy_json,
            "policy_hash": digest(authority.policy_json) if authority.policy_json else None,
            "webauthn": (verified.presentation if native else None),
            "presentation": (None if native else dict(verified.presentation)),
        }
        bundle = {
            "poai_version": "0.1",
            "order_id": order_id,
            "checkout_id": checkout_id,
            "adjudication": {
                "verdict": verdict.verdict,
                "reason_code": verdict.reason_code,
                "transcript": [dict(t) for t in verdict.transcript],
            },
            "aal": {
                "level": level,
                "reasons": list(reasons),
                "scheme": authority.scheme,
                "predicates": dict(verified.predicates),
            },
            "authority": authority_section,
        }
        bundle["bundle_id"] = digest(bundle)
        return bundle

    # -- read ------------------------------------------------------------
    def get_order(self, actor: Actor, checkout_id: str) -> OrderView:
        for o in self._orders.values():
            if o["checkout_id"] == checkout_id:
                return OrderView(
                    order_id=o["order_id"], checkout_id=checkout_id,
                    status=o["status"], total_minor=o["total_minor"],
                    aal_level=o["aal_level"],
                )
        raise ValueError(f"no order for checkout {checkout_id}")

    def get_evidence(self, actor: Actor, checkout_id: str) -> dict:
        for o in self._orders.values():
            if o["checkout_id"] == checkout_id:
                return o["bundle"]
        raise ValueError(f"no evidence for checkout {checkout_id}")
