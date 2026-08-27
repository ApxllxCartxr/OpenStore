import httpx
from pydantic import BaseModel


class MerchantDescriptor(BaseModel):
    name: str
    id: str


class AuthDescriptor(BaseModel):
    authorization_server: str
    scopes_supported: list[str]


class PolicyDescriptor(BaseModel):
    currency: str
    max_unconfirmed_spend_minor: int
    requires_human_approval: bool
    default_per_tx_cap_minor: int


class AgentCommerceDescriptor(BaseModel):
    version: str = "0.1"
    merchant: MerchantDescriptor
    storefront: str
    catalog_endpoint: str
    mcp_endpoint: str
    a2a_agent_card: str
    auth: AuthDescriptor
    policy: PolicyDescriptor


def discover_merchant(base_url: str) -> AgentCommerceDescriptor:
    """GET {base_url}/.well-known/agent-commerce.json, parse into the
    AgentCommerceDescriptor model. This is the ONLY thing buyer_agent
    knows about the merchant before this call returns."""
    resp = httpx.get(
        f"{base_url.rstrip('/')}/.well-known/agent-commerce.json", timeout=10.0
    )
    resp.raise_for_status()
    return AgentCommerceDescriptor(**resp.json())


def discover_auth_server(descriptor: AgentCommerceDescriptor) -> dict:
    """GET descriptor.auth.authorization_server, return the parsed
    RFC 8414 metadata dict (endpoints, supported grant types)."""
    resp = httpx.get(descriptor.auth.authorization_server, timeout=10.0)
    resp.raise_for_status()
    return resp.json()
