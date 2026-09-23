"""Agent Profile fetching. This is an SSRF sink and is hardened as one.

A stranger registering is a stranger making the sidecar issue an outbound
request to a URL they chose. That is the definition of SSRF, so the rules are
not advisory:

- HTTPS only.
- Public IPs only. Loopback, RFC 1918, link-local, and `169.254.169.254`
  refused — **before any fetch**, not after a redirect.
- Resolve, then pin. The address that passed the check is the address connected
  to, or DNS rebinding turns a passing check into a private fetch.
- No cross-host redirects.
- Hard size and timeout caps.
- Its own rate limit.

**The dev exception**, and it is exactly one (ADR-0012, §10.1): named
`host[:port]` entries, never a CIDR, and it permits plain `http` for exactly
those entries — because the compose demo's own chat is reachable only as
`http://buyer-chat:3001`, and HTTPS-only hardening would refuse the demo's own
self-registration. That is a genuinely confusing hour to spend, so the carve-out
is explicit. Everything else still applies: the metadata address stays refused
unconditionally even here, resolve-then-pin and the caps still run, every use is
logged and flagged in health output and the `/agentic` banner, and the sidecar
refuses to boot when the allowlist is non-empty alongside live provider keys.

A bypass that is silent, broad, or bootable in production is how bypasses reach
production.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from openstore.sidecar.core.codes import ReasonCode
from openstore.sidecar.trait.errors import TraitError

#: Refused unconditionally, dev allowlist included. Cloud metadata is the single
#: highest-value SSRF target there is.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254"})

MAX_PROFILE_BYTES = 64 * 1024
FETCH_TIMEOUT_SECONDS = 5


class ProfileRefused(TraitError):
    def __init__(self, detail: str) -> None:
        super().__init__(ReasonCode.PROFILE_REFUSED, detail)


@dataclass(frozen=True)
class AgentProfile:
    """A Buyer Agent's self-published document. It admits; it never authorizes
    a spend."""

    agent_id: str
    name: str
    contact: str
    jwks: dict[str, object]
    source_url: str
    callback_url: str = ""
    """Where this agent wants order events posted. Optional — an agent that
    declares none keeps polling `order-status`, which is the behaviour every
    agent had before events existed.

    Declared in the Profile rather than passed at registration so it is a fact
    the agent publishes about itself at a URL this sidecar already fetched and
    hardened, not a string an unauthenticated caller hands over."""


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def resolve(host: str) -> list[str]:
    """Resolve once. The caller pins what comes back."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ProfileRefused(f"cannot resolve {host!r}: {exc}") from None
    return sorted({info[4][0] for info in infos})


@dataclass
class ProfileFetcher:
    """One per deployment, holding the dev allowlist if there is one."""

    dev_hosts: tuple[str, ...] = ()
    resolver: object = None  # injected in tests; production uses `resolve`

    _warnings: list[str] = field(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        """Every use of the dev exception, for health output and the console
        banner. An exception nobody can see is one that outlives its reason."""
        return list(self._warnings)

    def _resolve(self, host: str) -> list[str]:
        if self.resolver is not None:
            return list(self.resolver(host))  # type: ignore[operator]
        return resolve(host)

    def check_url(self, url: str) -> tuple[str, list[str]]:
        """Validate a profile URL **before any fetch**, returning the host and
        the addresses to pin.

        Every refusal here happens without a packet leaving the box, which is
        the point: a check performed after the request has been made has already
        lost.
        """
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            raise ProfileRefused(f"{url!r} has no host")

        hostport = f"{host}:{parsed.port}" if parsed.port else host
        dev_allowed = hostport in self.dev_hosts or host in self.dev_hosts

        if parsed.scheme == "http" and not dev_allowed:
            raise ProfileRefused(
                "Agent Profiles are fetched over HTTPS only. (The compose demo's own "
                "chat is the one exception and must be named in OPENSTORE_DEV_PROFILE_HOSTS.)"
            )
        if parsed.scheme not in {"http", "https"}:
            raise ProfileRefused(f"{parsed.scheme!r} is not a scheme we fetch")

        # The metadata address is refused before the allowlist is consulted, so
        # no configuration can reach it.
        if host in METADATA_ADDRESSES:
            raise ProfileRefused("the cloud metadata address is refused unconditionally")

        addresses = self._resolve(host)
        if not addresses:
            raise ProfileRefused(f"{host!r} resolves to nothing")

        for address in addresses:
            if address in METADATA_ADDRESSES:
                raise ProfileRefused(
                    f"{host!r} resolves to the cloud metadata address; refused unconditionally"
                )
            if not _is_public(address) and not dev_allowed:
                raise ProfileRefused(
                    f"{host!r} resolves to {address}, which is not a public address. "
                    f"Agent Profiles must be reachable from the internet."
                )

        if dev_allowed:
            self._warnings.append(f"dev profile allowlist admitted {hostport} — development only")
        return host, addresses

    async def fetch(self, url: str, *, client: httpx.AsyncClient | None = None) -> AgentProfile:
        """Fetch, with the caps and the redirect ban applied."""
        host, addresses = self.check_url(url)

        owns = client is None
        http = client or httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=False,  # a cross-host redirect is the bypass
        )
        try:
            response = await http.get(url)
        except httpx.HTTPError as exc:
            raise ProfileRefused(f"could not fetch {url!r}: {exc}") from None
        finally:
            if owns:
                await http.aclose()

        if response.status_code in {301, 302, 303, 307, 308}:
            raise ProfileRefused(
                "Agent Profile URLs are fetched without following redirects — a redirect "
                "is how a public host becomes a private one after the check has passed"
            )
        if response.status_code != 200:
            raise ProfileRefused(f"{url!r} answered HTTP {response.status_code}")
        if len(response.content) > MAX_PROFILE_BYTES:
            raise ProfileRefused(
                f"profile is {len(response.content)} bytes; the cap is {MAX_PROFILE_BYTES}"
            )

        try:
            document = response.json()
        except ValueError:
            raise ProfileRefused("profile is not JSON") from None

        return parse_profile(document, source_url=url)


def parse_profile(document: object, *, source_url: str) -> AgentProfile:
    """Parse and derive `agent_id`, failing loud on anything missing.

    `agent_id` is the RFC 7638 JWK thumbprint of the profile's first key, so it
    is derived from the key rather than claimed by the agent — a self-declared
    id would let two agents claim to be the same one.
    """
    if not isinstance(document, dict):
        raise ProfileRefused("profile must be a JSON object")
    jwks = document.get("jwks")
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list) or not jwks["keys"]:
        raise ProfileRefused("profile carries no JWKS")

    key = jwks["keys"][0]
    if not isinstance(key, dict) or key.get("kty") != "EC" or key.get("crv") != "P-256":
        raise ProfileRefused("Agent Profile keys are ES256 (EC/P-256)")

    return AgentProfile(
        agent_id=jwk_thumbprint(key),
        name=str(document.get("name", "")),
        contact=str(document.get("contact", "")),
        jwks=jwks,
        source_url=source_url,
        callback_url=str(document.get("callback_url", "")),
    )


def jwk_thumbprint(key: dict[str, object]) -> str:
    """RFC 7638. The required members for EC, in lexicographic order, with no
    whitespace — the spec is precise about this because the whole value of a
    thumbprint is that two implementations compute the same one."""
    import base64
    import hashlib
    import json

    required = {"crv": key["crv"], "kty": key["kty"], "x": key["x"], "y": key["y"]}
    canonical = json.dumps(required, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
