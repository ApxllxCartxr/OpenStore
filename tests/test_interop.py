"""INTEROP_SPEC §7 conformance suite. Test names are normative.

These prove the central claim: the authorization decision is protocol-independent
(`test_same_cart_same_verdict_across_protocols`), adapters translate without
touching money/persistence (`test_adapters_do_not_touch_persistence`,
`test_no_adapter_accepts_a_price`), every advertised protocol was built against a
fetched spec (`test_every_adapter_has_a_dated_spec_excerpt`), and unknown schemes
or unmappable constraints fail closed (`test_unknown_scheme_is_rejected`,
`test_ap2_unmappable_constraint_fails_closed`).
"""

from __future__ import annotations

import ast
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openstore.core.api import CommerceCore
from openstore.core.authority import (
    MAX_AAL_BY_SCHEME, UnknownSchemeError, cap_aal, verify,
)
from openstore.core.types import Actor, AuthorityPresentation, LineItemRequest
from openstore.models import Policy
from openstore.protocols import (
    PROTOCOLS, has_spec_excerpt, protocols_doc, read_excerpt,
)
from openstore.protocols.acp.adapter import AcpAdapter, ACP_EXTERNAL_FIELDS, ACP_STATE_BY_CHECKOUT_STATUS
from openstore.protocols.a2a import A2A_EXTERNAL_FIELDS
from openstore.protocols.ap2.adapter import (
    AP2UnmappableConstraint, AP2_EXTERNAL_FIELDS, ingest_mandate,
)
from openstore.protocols.mcp.adapter import MCP_EXTERNAL_FIELDS, McpAdapter
from openstore.runtime import MerchantRuntime

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rt(tmp_path):
    return MerchantRuntime(
        merchant_signing_key=Ed25519PrivateKey.generate(),
        legal_name="Gelateria", country="IN",
        per_txn_limit=1_000_000_00, daily_limit=5_000_000_00,
        catalog={"GELATO": {"unit_price_paise": 5000, "tags": ["food"]},
                 "CONE": {"unit_price_paise": 2000, "tags": ["food"]}},
        policy=Policy(spend_limit_paise=1_000_000_00),
        rp_id="openstore.local", origin="https://openstore.local",
        ledger_url=f"sqlite:///{tmp_path / 'ledger.db'}",
    )


def _mcp_actor(subject="buyer", scopes=("cart:write", "checkout:confirm")):
    return Actor(subject=subject, display_name=subject, scopes=frozenset(scopes),
                 protocol="mcp", protocol_session_id=None, auth_method="oauth2_bearer")


# ---------------------------------------------------------------------------
# §7 — structural / architectural
# ---------------------------------------------------------------------------
def test_core_is_protocol_free():
    core_dir = os.path.join(_REPO, "openstore", "core")
    banned = ("mcp", "fastapi", "a2a", "starlette", "openstore.protocols")
    for root, _d, files in os.walk(core_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(root, fn), encoding="utf-8") as fh:
                src = fh.read()
            tree = ast.parse(src, filename=fn)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for n in node.names:
                        assert not any(b in n.name.split(".") for b in banned), \
                            f"openstore/core imports protocol lib {n.name}"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert not any(b in node.module.split(".") for b in banned), \
                        f"openstore/core imports protocol lib {node.module}"


def test_adapters_do_not_touch_persistence():
    adapter_files = [
        os.path.join(_REPO, "openstore", "protocols", "mcp", "adapter.py"),
        os.path.join(_REPO, "openstore", "protocols", "acp", "adapter.py"),
        os.path.join(_REPO, "openstore", "protocols", "ap2", "adapter.py"),
        os.path.join(_REPO, "openstore", "protocols", "ap2", "emit.py"),
        os.path.join(_REPO, "openstore", "protocols", "a2a", "__init__.py"),
    ]
    banned_tokens = ("sqlmodel", "razorpay", "IdempotencyRecord", "SpendLedgerEntry",
                     "openstore.models", "sessionmaker")
    for path in adapter_files:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for tok in banned_tokens:
            assert tok not in src, f"{path} references persistence token {tok!r}"


def test_every_adapter_has_a_dated_spec_excerpt():
    for name in PROTOCOLS:
        assert has_spec_excerpt(name), f"{name} missing a dated SPEC_EXCERPT.md"
        text = read_excerpt(name)
        assert "http" in text, f"{name} excerpt has no source URL"
        assert "-" in text and "20" in text, f"{name} excerpt has no ISO date"


def test_every_external_field_is_in_the_mapping_table():
    mapping = {
        "mcp": MCP_EXTERNAL_FIELDS,
        "acp": ACP_EXTERNAL_FIELDS,
        "ap2": AP2_EXTERNAL_FIELDS,
        "a2a": A2A_EXTERNAL_FIELDS,
    }
    for name, fields in mapping.items():
        md_path = os.path.join(_REPO, "openstore", "protocols", name, "mapping.md")
        with open(md_path, encoding="utf-8") as fh:
            md_text = fh.read()
        for field in fields:
            assert field in md_text, f"{name}: external field {field!r} missing from mapping.md"


def test_discovery_lists_only_passing_protocols(monkeypatch):
    from openstore.protocols.mcp import conformance
    monkeypatch.setattr(conformance, "conformance_pass", lambda: False)
    names = [p["name"] for p in protocols_doc()]
    assert "mcp" not in names
    # The others still pass.
    assert "acp" in names and "ap2" in names and "a2a" in names
    monkeypatch.undo()


def test_no_adapter_accepts_a_price(tmp_path):
    core = CommerceCore(_rt(tmp_path))
    mcp = McpAdapter(core)
    acp = AcpAdapter(core)

    # MCP: smuggle a price; the core-bound LineItemRequest must ignore it.
    cart = mcp.create_cart([{"sku": "GELATO", "qty": 2, "price": 1}], client_id="buyer")
    assert not hasattr(cart.items[0], "price")
    assert cart.total_minor == 5000 * 2, "total must use catalog price, not the smuggled price"

    # ACP: same discipline.
    sess = acp.create_checkout_session("s1", [{"sku": "GELATO", "qty": 1, "amount": 999}],
                                       buyer={"agent_id": "buyer"})
    assert sess["amount"]["value"] == 5000, "ACP total must use catalog price"


def test_budget_is_shared_across_protocols(tmp_path):
    core = CommerceCore(_rt(tmp_path))
    subject = "shared-buyer"
    actor_mcp = _mcp_actor(subject)
    actor_acp = Actor(subject=subject, display_name="a", scopes=frozenset(("checkout:confirm",)),
                      protocol="acp", protocol_session_id="s1", auth_method="oauth2_bearer")

    cart = core.create_cart(actor_mcp, (LineItemRequest(sku="GELATO", qty=1),))
    co = core.initiate_checkout(actor_mcp, cart.cart_id, __import__("openstore.core.types", fromlist=["DeliveryAddress"]).DeliveryAddress(raw=""))
    before = core.remaining_budget(subject)
    core.confirm_checkout(actor_mcp, co.checkout_id,
                          AuthorityPresentation(scheme="native_webauthn", raw={}, policy_json=None), "idem-1")
    after_mcp = core.remaining_budget(subject)
    assert after_mcp == before - 5000

    # Spend over MCP is now visible to an ACP-side confirm for the same subject.
    cart2 = core.create_cart(actor_acp, (LineItemRequest(sku="CONE", qty=1),))
    co2 = core.initiate_checkout(actor_acp, cart2.cart_id, __import__("openstore.core.types", fromlist=["DeliveryAddress"]).DeliveryAddress(raw=""))
    core.confirm_checkout(actor_acp, co2.checkout_id,
                          AuthorityPresentation(scheme="acp_delegated_token", raw={"token_present": True}, policy_json=None), "idem-2")
    after_acp = core.remaining_budget(subject)
    assert after_acp == after_mcp - 2000


def test_scheme_cap_is_applied():
    # All predicates true -> resolve_aal gives 3; cap per scheme must bind.
    from openstore.aal import Predicates, resolve_aal
    preds = Predicates(
        e1_agent_authenticated=True, e2_policy_signature_valid=True, e3_assertion_fresh=True,
        e4_user_verified=True, e5_cart_bound=True, e6_catalog_attested=True,
        e7_compiler_allow=True, e8_intent_recorded=True, e9_notified=True,
    )
    level, reasons = resolve_aal(preds, 2)
    assert level == 3
    for scheme, cap in MAX_AAL_BY_SCHEME.items():
        lvl, rsns = cap_aal(level, reasons, scheme)
        assert lvl == cap, f"{scheme} not capped to {cap}"
        if cap < level:
            assert f"scheme_capped_{scheme}" in rsns


def test_unknown_scheme_is_rejected():
    with pytest.raises(UnknownSchemeError) as exc:
        verify(AuthorityPresentation(scheme="bogus_scheme", raw={}, policy_json=None))
    assert "unknown_scheme" in str(exc.value)


def test_ap2_unmappable_constraint_fails_closed():
    mandate = {"type": "cart_mandate", "vct": "mandate.checkout.closed.1",
               "constraints": {"recurrence": "weekly"}, "signature": {}}
    with pytest.raises(AP2UnmappableConstraint) as exc:
        ingest_mandate(mandate)
    assert "ap2.unmappable_constraint" in str(exc.value)


def test_same_cart_same_verdict_across_protocols(tmp_path):
    core = CommerceCore(_rt(tmp_path))
    subject = "buyer"
    items = (LineItemRequest(sku="GELATO", qty=1),)
    Delivery = __import__("openstore.core.types", fromlist=["DeliveryAddress"]).DeliveryAddress

    def run(protocol, scheme):
        core._spend[subject] = 0
        core._txcount[subject] = 0
        actor = Actor(subject=subject, display_name="b",
                      scopes=frozenset(("cart:write", "checkout:confirm")),
                      protocol=protocol, protocol_session_id=None, auth_method="oauth2_bearer")
        cart = core.create_cart(actor, items)
        co = core.initiate_checkout(actor, cart.cart_id, Delivery(raw=""))
        res = core.confirm_checkout(
            actor, co.checkout_id,
            AuthorityPresentation(scheme=scheme, raw={}, policy_json=None),
            f"idem-{protocol}")
        bundle = core.get_evidence(actor, co.checkout_id)
        return json.dumps(bundle["adjudication"]["transcript"], sort_keys=True)

    mcp_transcript = run("mcp", "native_webauthn")
    acp_transcript = run("acp", "acp_delegated_token")
    assert mcp_transcript == acp_transcript


def test_merchant_agent_cannot_call_core_writes():
    agent_dir = os.path.join(_REPO, "merchant_agent")
    banned = ("create_cart", "update_cart", "initiate_checkout", "confirm_checkout")
    if not os.path.isdir(agent_dir):
        pytest.skip("no merchant_agent package")
    for root, _d, files in os.walk(agent_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(root, fn), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=fn)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("openstore.core.api"):
                    for n in node.names:
                        assert n.name not in banned, f"{fn} imports core write {n.name}"
                if isinstance(node, ast.Import):
                    for n in node.names:
                        assert n.name != "openstore.core.api", f"{fn} imports openstore.core.api"
