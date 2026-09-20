"""Two admission routes, one authority.

An allowlisted agent uses Merchant-issued OAuth client credentials; a stranger
self-registers by publishing an Agent Profile and signing every request against
it (ADR-0012). Both get the same four scopes and the same short expiry.

**No tier unlocks money.** `confirm` without a fresh accepted Authority is
refused by the Gate regardless of which door the agent came through. Reputation
buys throughput only — an allowlisted agent gets a higher rate limit and not one
capability more.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from openstore.sidecar.admission.ratelimit import Tier
from openstore.sidecar.core.codes import ReasonCode, Scope
from openstore.sidecar.trait.errors import TraitError

#: §16.7. Short, because an agent token is a bearer credential and the agent is
#: a stranger by design.
TOKEN_TTL = timedelta(minutes=15)

ALL_SCOPES: frozenset[Scope] = frozenset(Scope)


@dataclass(frozen=True)
class AgentToken:
    token: str
    agent_id: str
    tier: Tier
    scopes: frozenset[Scope]
    expires_at: datetime

    def allows(self, scope: Scope, *, now: datetime | None = None) -> bool:
        moment = now or datetime.now(UTC)
        return scope in self.scopes and moment <= self.expires_at


@dataclass
class Admission:
    """Issues tokens by either route, and refuses blocklisted agents."""

    clients: dict[str, str] = field(default_factory=dict)
    """client_id -> client_secret, Merchant-issued in `/agentic`."""
    blocklist: set[str] = field(default_factory=set)
    tokens: dict[str, AgentToken] = field(default_factory=dict)

    def issue_for_client(self, client_id: str, client_secret: str) -> AgentToken:
        expected = self.clients.get(client_id)
        # compare_digest so a wrong secret cannot be found a character at a time.
        import hmac

        if expected is None or not hmac.compare_digest(expected, client_secret):
            raise TraitError(ReasonCode.SIGNATURE_INVALID, "client credentials are not valid")
        self._refuse_if_blocked(client_id)
        return self._issue(client_id, Tier.ALLOWLISTED)

    def issue_for_stranger(self, agent_id: str) -> AgentToken:
        """Issued on the spot, with no prior Merchant action. That is the
        product: admission is open by design, and the Gate is what makes it
        safe."""
        self._refuse_if_blocked(agent_id)
        return self._issue(agent_id, Tier.SELF_REGISTERED)

    def _refuse_if_blocked(self, agent_id: str) -> None:
        if agent_id in self.blocklist:
            raise TraitError(ReasonCode.AGENT_BLOCKED, "this agent is blocked by the Merchant")

    def _issue(self, agent_id: str, tier: Tier) -> AgentToken:
        token = AgentToken(
            token=secrets.token_urlsafe(24),
            agent_id=agent_id,
            tier=tier,
            # Identical scopes on both routes. A tier that carried extra scopes
            # would be a tier that unlocks money.
            scopes=ALL_SCOPES,
            expires_at=datetime.now(UTC) + TOKEN_TTL,
        )
        self.tokens[token.token] = token
        return token

    def resolve(self, token: str, *, now: datetime | None = None) -> AgentToken:
        record = self.tokens.get(token)
        if record is None:
            raise TraitError(ReasonCode.SIGNATURE_INVALID, "unknown token")
        if (now or datetime.now(UTC)) > record.expires_at:
            raise TraitError(ReasonCode.SIGNATURE_INVALID, "token expired")
        if record.agent_id in self.blocklist:
            # Revocation invalidates the future, not the past: an already-issued
            # token stops working the moment the Merchant blocks the agent.
            raise TraitError(ReasonCode.AGENT_BLOCKED, "this agent is blocked by the Merchant")
        return record

    def revoke_agent(self, agent_id: str) -> None:
        self.blocklist.add(agent_id)
