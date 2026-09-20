"""Admission: two routes, one authority. And the SSRF sink, hardened as one."""

from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from openstore.sidecar.admission.oauth import Admission
from openstore.sidecar.admission.profile import (
    METADATA_ADDRESSES,
    ProfileFetcher,
    ProfileRefused,
    jwk_thumbprint,
    parse_profile,
)
from openstore.sidecar.admission.ratelimit import RateLimiter, Tier
from openstore.sidecar.admission.signatures import (
    SignatureRefused,
    SignatureVerifier,
    content_digest,
    public_jwk,
    sign_for_tests,
)
from openstore.sidecar.core.codes import ReasonCode, Scope
from openstore.sidecar.trait.errors import TraitError


def _public_resolver(mapping: dict[str, list[str]]):
    return lambda host: mapping.get(host, ["93.184.216.34"])


# ── The SSRF sink ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("https://localhost/.well-known/agent-profile.json", ["127.0.0.1"]),
        ("https://internal.example/.well-known/agent-profile.json", ["10.0.0.5"]),
        ("https://internal.example/.well-known/agent-profile.json", ["192.168.1.9"]),
        ("https://internal.example/.well-known/agent-profile.json", ["172.16.4.4"]),
        ("https://link.example/.well-known/agent-profile.json", ["169.254.1.1"]),
    ],
    ids=["loopback", "rfc1918-10", "rfc1918-192", "rfc1918-172", "link-local"],
)
def test_private_addresses_are_refused_before_any_fetch(url: str, addresses: list[str]) -> None:
    """A check performed after the request has been made has already lost."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    fetcher = ProfileFetcher(resolver=_public_resolver({host: addresses}))
    with pytest.raises(ProfileRefused, match="public address"):
        fetcher.check_url(url)


def test_the_metadata_address_is_refused_unconditionally() -> None:
    fetcher = ProfileFetcher(dev_hosts=("169.254.169.254",))
    with pytest.raises(ProfileRefused, match="unconditionally"):
        fetcher.check_url("https://169.254.169.254/latest/meta-data/")


def test_the_metadata_address_is_refused_even_by_resolution() -> None:
    """The name is innocent and the answer is not — which is why the check runs
    on what the host resolves to, not on what it is called."""
    fetcher = ProfileFetcher(
        dev_hosts=("evil.example",),
        resolver=_public_resolver({"evil.example": ["169.254.169.254"]}),
    )
    with pytest.raises(ProfileRefused, match="unconditionally"):
        fetcher.check_url("https://evil.example/p.json")


def test_plain_http_is_refused_unless_the_host_is_named() -> None:
    fetcher = ProfileFetcher()
    with pytest.raises(ProfileRefused, match="HTTPS only"):
        fetcher.check_url("http://agent.example/p.json")


def test_the_dev_allowlist_admits_exactly_its_named_host() -> None:
    """§10.1's carve-out: the compose demo's own chat is reachable only as
    `http://buyer-chat:3001`, and HTTPS-only hardening would refuse the demo's
    own self-registration."""
    fetcher = ProfileFetcher(
        dev_hosts=("buyer-chat:3001",),
        resolver=_public_resolver({"buyer-chat": ["172.18.0.4"], "other": ["172.18.0.5"]}),
    )
    host, _ = fetcher.check_url("http://buyer-chat:3001/.well-known/agent-profile.json")
    assert host == "buyer-chat"

    # Same private range, different name: refused on both dimensions, because
    # the allowlist is names and not a CIDR — a range is not an exception, it
    # is a hole. Plain http is refused first (scheme), and the private address
    # is refused too if it ever gets that far.
    with pytest.raises(ProfileRefused, match="HTTPS only"):
        fetcher.check_url("http://other:3001/.well-known/agent-profile.json")
    with pytest.raises(ProfileRefused, match="public address"):
        fetcher.check_url("https://other/.well-known/agent-profile.json")


def test_every_use_of_the_dev_allowlist_is_flagged() -> None:
    """An exception nobody can see is one that outlives its reason (SPEC §14)."""
    fetcher = ProfileFetcher(
        dev_hosts=("buyer-chat:3001",),
        resolver=_public_resolver({"buyer-chat": ["172.18.0.4"]}),
    )
    fetcher.check_url("http://buyer-chat:3001/p.json")
    assert any("dev profile allowlist" in w for w in fetcher.warnings)


def test_a_public_profile_url_passes_with_the_allowlist_unset() -> None:
    """C2's real gate: the compose path proves the demo runs, this proves the
    design does."""
    fetcher = ProfileFetcher(resolver=_public_resolver({"agent.example": ["93.184.216.34"]}))
    host, addresses = fetcher.check_url("https://agent.example/.well-known/agent-profile.json")
    assert host == "agent.example"
    assert addresses == ["93.184.216.34"]
    assert fetcher.warnings == []


def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(ProfileRefused, match="no host"):
        ProfileFetcher().check_url("file:///etc/passwd")


def test_metadata_addresses_are_a_closed_set() -> None:
    assert "169.254.169.254" in METADATA_ADDRESSES


# ── Profile parsing ──────────────────────────────────────────────────────────


def _profile_doc(kid: str = "k1") -> dict[str, object]:
    key = ec.generate_private_key(ec.SECP256R1())
    return {
        "name": "Demo chat",
        "contact": "demo@spoiledduckie.test",
        "jwks": {"keys": [public_jwk(key, kid)]},
    }


def test_agent_id_is_the_rfc7638_thumbprint_not_a_claim() -> None:
    """Derived from the key, so two agents cannot claim to be the same one."""
    doc = _profile_doc()
    profile = parse_profile(doc, source_url="https://agent.example/p.json")
    assert profile.agent_id == jwk_thumbprint(doc["jwks"]["keys"][0])  # type: ignore[index]
    assert len(profile.agent_id) == 43  # base64url of a sha256, unpadded


def test_a_profile_with_no_jwks_is_refused() -> None:
    with pytest.raises(ProfileRefused, match="no JWKS"):
        parse_profile({"name": "x"}, source_url="https://a/p.json")


def test_a_non_es256_key_is_refused() -> None:
    doc = {"jwks": {"keys": [{"kty": "RSA", "n": "x", "e": "AQAB"}]}}
    with pytest.raises(ProfileRefused, match="ES256"):
        parse_profile(doc, source_url="https://a/p.json")


# ── RFC 9421 ─────────────────────────────────────────────────────────────────


@pytest.fixture
def signing_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _sign(key: ec.EllipticCurvePrivateKey, body: bytes = b'{"q":"tote"}', **overrides: object):
    now = int(time.time())
    params = {
        "method": "POST",
        "target_uri": "https://spoiledduckie.localhost/agent/search",
        "body": body,
        "created": now,
        "expires": now + 30,
        "nonce": "nonce-1",
        "key_id": "k1",
    }
    params.update(overrides)
    signature, digest = sign_for_tests(key, **params)  # type: ignore[arg-type]
    return signature, digest, params


def test_a_valid_signature_verifies(signing_key: ec.EllipticCurvePrivateKey) -> None:
    signature, digest, params = _sign(signing_key)
    SignatureVerifier().verify(
        jwks={"keys": [public_jwk(signing_key, "k1")]},
        content_digest_header=digest,
        signature_b64=signature,
        **params,
    )


def test_a_corrupted_signature_base_is_rejected(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """A4c's gate, asserted from the verifier's end."""
    signature, digest, params = _sign(signing_key)
    params["target_uri"] = "https://spoiledduckie.localhost/agent/place-order"
    with pytest.raises(SignatureRefused, match="does not verify"):
        SignatureVerifier().verify(
            jwks={"keys": [public_jwk(signing_key, "k1")]},
            content_digest_header=digest,
            signature_b64=signature,
            **params,
        )


def test_a_swapped_body_is_named_as_a_swapped_body(
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    signature, digest, params = _sign(signing_key)
    params["body"] = b'{"q":"something else"}'
    with pytest.raises(SignatureRefused, match="modified in flight"):
        SignatureVerifier().verify(
            jwks={"keys": [public_jwk(signing_key, "k1")]},
            content_digest_header=digest,
            signature_b64=signature,
            **params,
        )


def test_a_replayed_nonce_is_refused(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """Replay dies here, not in review."""
    verifier = SignatureVerifier()
    jwks = {"keys": [public_jwk(signing_key, "k1")]}
    signature, digest, params = _sign(signing_key)
    verifier.verify(jwks=jwks, content_digest_header=digest, signature_b64=signature, **params)
    with pytest.raises(SignatureRefused, match="already been seen"):
        verifier.verify(jwks=jwks, content_digest_header=digest, signature_b64=signature, **params)


def test_a_stale_signature_is_refused(signing_key: ec.EllipticCurvePrivateKey) -> None:
    now = int(time.time())
    signature, digest, params = _sign(signing_key, created=now - 300, expires=now - 240)
    with pytest.raises(SignatureRefused, match="expired"):
        SignatureVerifier().verify(
            jwks={"keys": [public_jwk(signing_key, "k1")]},
            content_digest_header=digest,
            signature_b64=signature,
            **params,
        )


def test_an_over_long_window_is_refused(signing_key: ec.EllipticCurvePrivateKey) -> None:
    """A signature good for longer than a minute is a signature worth
    capturing."""
    now = int(time.time())
    signature, digest, params = _sign(signing_key, created=now, expires=now + 3600)
    with pytest.raises(SignatureRefused, match="window is"):
        SignatureVerifier().verify(
            jwks={"keys": [public_jwk(signing_key, "k1")]},
            content_digest_header=digest,
            signature_b64=signature,
            **params,
        )


def test_a_signature_from_another_key_is_refused(
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    signature, digest, params = _sign(signing_key)
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(SignatureRefused, match="does not verify"):
        SignatureVerifier().verify(
            jwks={"keys": [public_jwk(other, "k1")]},
            content_digest_header=digest,
            signature_b64=signature,
            **params,
        )


def test_a_failed_verification_does_not_burn_the_nonce(
    signing_key: ec.EllipticCurvePrivateKey,
) -> None:
    """Remembering a nonce from an unverified request lets anyone burn nonces
    they never signed."""
    verifier = SignatureVerifier()
    other = ec.generate_private_key(ec.SECP256R1())
    signature, digest, params = _sign(signing_key)

    with pytest.raises(SignatureRefused):
        verifier.verify(
            jwks={"keys": [public_jwk(other, "k1")]},
            content_digest_header=digest,
            signature_b64=signature,
            **params,
        )
    # The real request, same nonce, now succeeds.
    verifier.verify(
        jwks={"keys": [public_jwk(signing_key, "k1")]},
        content_digest_header=digest,
        signature_b64=signature,
        **params,
    )


def test_content_digest_is_rfc9530_structured_field() -> None:
    value = content_digest(b"hello")
    assert value.startswith("sha-256=:") and value.endswith(":")


# ── Two routes, one authority ────────────────────────────────────────────────


def test_both_routes_get_identical_scopes() -> None:
    """Reputation buys throughput only. A tier carrying extra scopes would be a
    tier that unlocks money."""
    admission = Admission(clients={"client_1": "secret_1"})
    allowlisted = admission.issue_for_client("client_1", "secret_1")
    stranger = admission.issue_for_stranger("thumbprint_abc")

    assert allowlisted.scopes == stranger.scopes == frozenset(Scope)
    assert allowlisted.tier is Tier.ALLOWLISTED
    assert stranger.tier is Tier.SELF_REGISTERED


def test_a_stranger_is_admitted_with_no_prior_merchant_action() -> None:
    token = Admission().issue_for_stranger("thumbprint_new")
    assert token.allows(Scope.CONFIRM), "confirm is granted; the Gate is what refuses the spend"


def test_a_blocklisted_profile_is_refused_at_registration() -> None:
    admission = Admission(blocklist={"thumbprint_bad"})
    with pytest.raises(TraitError) as exc:
        admission.issue_for_stranger("thumbprint_bad")
    assert exc.value.code is ReasonCode.AGENT_BLOCKED


def test_revocation_invalidates_an_already_issued_token() -> None:
    """Revocation invalidates the future, not the past — but the future starts
    immediately, not at token expiry."""
    admission = Admission()
    token = admission.issue_for_stranger("thumbprint_x")
    admission.resolve(token.token)
    admission.revoke_agent("thumbprint_x")
    with pytest.raises(TraitError) as exc:
        admission.resolve(token.token)
    assert exc.value.code is ReasonCode.AGENT_BLOCKED


def test_wrong_client_credentials_are_refused() -> None:
    admission = Admission(clients={"client_1": "secret_1"})
    with pytest.raises(TraitError):
        admission.issue_for_client("client_1", "wrong")


# ── Rate limits ──────────────────────────────────────────────────────────────


def test_self_registered_agents_sit_in_the_low_tier() -> None:
    limiter = RateLimiter()
    for _ in range(30):
        limiter.check("agent", "a1", tier=Tier.SELF_REGISTERED, now=100.0)
    with pytest.raises(TraitError) as exc:
        limiter.check("agent", "a1", tier=Tier.SELF_REGISTERED, now=100.0)
    assert exc.value.code is ReasonCode.RATE_LIMITED


def test_allowlisted_agents_sit_in_the_high_tier() -> None:
    limiter = RateLimiter()
    for _ in range(300):
        limiter.check("agent", "a2", tier=Tier.ALLOWLISTED, now=100.0)
    with pytest.raises(TraitError):
        limiter.check("agent", "a2", tier=Tier.ALLOWLISTED, now=100.0)


def test_the_window_slides() -> None:
    limiter = RateLimiter()
    for _ in range(30):
        limiter.check("agent", "a3", now=100.0)
    limiter.check("agent", "a3", now=161.0)


@pytest.mark.parametrize(
    ("bucket", "limit"),
    [
        ("profile-registration", 5),
        ("tap-issuance", 5),
        ("approve-attempt", 10),
        ("code-attempt", 5),
        ("quantity-oracle", 20),
    ],
)
def test_each_oracle_has_its_own_throttle(bucket: str, limit: int) -> None:
    """Separate buckets, because each is its own oracle. Sharing one would let
    an attacker spend another's budget."""
    limiter = RateLimiter()
    for _ in range(limit):
        limiter.check(bucket, "subject", now=100.0)
    with pytest.raises(TraitError):
        limiter.check(bucket, "subject", now=100.0)


def test_a_rate_limit_refusal_does_not_count_down() -> None:
    """A countdown is a tuning signal for whoever is probing."""
    limiter = RateLimiter()
    for _ in range(5):
        limiter.check("code-attempt", "ord_1", now=100.0)
    with pytest.raises(TraitError) as exc:
        limiter.check("code-attempt", "ord_1", now=100.0)
    assert "remaining" not in exc.value.detail


# ── A4c: the shared vectors, from the verifier's end ─────────────────────────


def _vectors() -> dict[str, object]:
    import json
    from pathlib import Path

    return json.loads(Path("tests/GOLDEN/rfc9421/vectors.json").read_text(encoding="utf-8"))


def test_the_shared_vectors_verify_here_too() -> None:
    """The **same fixture** `demo/buyer-chat/tests/signature-vectors.test.ts`
    asserts against.

    A4c's whole point: the signer and the verifier were written in one sitting
    against one set of vectors, so a drift on either end fails on both. Without
    a shared fixture the two halves agree only until somebody edits one.
    """
    from openstore.sidecar.admission.signatures import content_digest, signature_base

    data = _vectors()
    jwks = {"keys": [data["public_jwk"]]}
    for case in data["cases"]:  # type: ignore[union-attr]
        body = str(case["body"]).encode()
        assert content_digest(body) == case["content_digest"], case["name"]
        assert (
            signature_base(
                method=str(case["method"]),
                target_uri=str(case["target_uri"]),
                content_digest_value=str(case["content_digest"]),
                created=int(case["created"]),  # type: ignore[arg-type]
                expires=int(case["expires"]),  # type: ignore[arg-type]
                nonce=str(case["nonce"]),
                key_id=str(case["key_id"]),
            ).decode()
            == case["signature_base"]
        ), case["name"]

        SignatureVerifier().verify(
            jwks=jwks,
            method=str(case["method"]),
            target_uri=str(case["target_uri"]),
            body=body,
            content_digest_header=str(case["content_digest"]),
            signature_b64=str(case["signature"]),
            created=int(case["created"]),  # type: ignore[arg-type]
            expires=int(case["expires"]),  # type: ignore[arg-type]
            nonce=str(case["nonce"]),
            key_id=str(case["key_id"]),
            now=int(case["created"]),  # type: ignore[arg-type]
        )


def test_a_corrupted_vector_is_rejected_with_a_named_code() -> None:
    """A4c's gate from this end: the chat asserts the same refusal."""
    data = _vectors()
    case = data["cases"][0]  # type: ignore[index]
    with pytest.raises(SignatureRefused, match="does not verify"):
        SignatureVerifier().verify(
            jwks={"keys": [data["public_jwk"]]},
            method=str(case["method"]),
            # The redirected target — same signature, different request.
            target_uri="https://spoiledduckie.localhost/agent/place-order",
            body=str(case["body"]).encode(),
            content_digest_header=str(case["content_digest"]),
            signature_b64=str(case["signature"]),
            created=int(case["created"]),
            expires=int(case["expires"]),
            nonce=str(case["nonce"]),
            key_id=str(case["key_id"]),
            now=int(case["created"]),
        )
