import time

from openstore.core import did, envelope, merkle, signing


def _keypair():
    priv, pub = did.generate_keypair()
    return priv, did.did_from_pubkey(pub)


def test_envelope_roundtrip():
    priv, d = _keypair()
    env = envelope.AgentTrustEnvelope(
        issuer_did=d,
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
        payload_type="application/json",
        payload=b'{"x":1}',
    ).sign(priv)
    env.verify()  # no raise
    assert env.digest() == envelope.AgentTrustEnvelope.from_dict(env.to_dict()).digest()


def test_envelope_tamper_detected():
    priv, d = _keypair()
    env = envelope.AgentTrustEnvelope(
        issuer_did=d,
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
        payload_type="application/json",
        payload=b'{"x":1}',
    ).sign(priv)
    tampered = envelope.AgentTrustEnvelope.from_dict(env.to_dict())
    tampered.payload = b'{"x":2}'
    try:
        tampered.verify()
        assert False, "should have raised"
    except ValueError as e:
        assert "digest mismatch" in str(e)


def test_envelope_expired():
    priv, d = _keypair()
    env = envelope.AgentTrustEnvelope(
        issuer_did=d,
        issued_at=int(time.time()) - 7200,
        expires_at=int(time.time()) - 3600,
        payload_type="application/json",
        payload=b"{}",
    ).sign(priv)
    try:
        env.verify()
        assert False
    except ValueError as e:
        assert "expired" in str(e)


def test_delegation_depth_guard():
    priv, d = _keypair()
    env = envelope.AgentTrustEnvelope(
        issuer_did=d,
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
        payload_type="application/json",
        payload=b"{}",
        delegation={"delegator_did": d, "scope": "*", "max_depth": 3, "depth": 4},
    ).sign(priv)
    try:
        env.verify()
        assert False
    except ValueError as e:
        assert "depth" in str(e)


def test_merkle_inclusion():
    ids = [f"R{i:03d}" for i in range(20)]
    leaves = [merkle.merkle_leaf(i) for i in ids]
    root, proofs = merkle.build_tree(leaves)
    for i in ids:
        assert merkle.verify_inclusion(i, root, proofs[merkle.merkle_leaf(i).hex()])


def test_merkle_inclusion_fails_on_wrong_id():
    ids = ["R001", "R002"]
    leaves = [merkle.merkle_leaf(i) for i in ids]
    root, proofs = merkle.build_tree(leaves)
    assert not merkle.verify_inclusion("R999", root, proofs[merkle.merkle_leaf("R001").hex()])


def test_daily_seed_deterministic_and_anchor():
    s = b"merchant-secret"
    a = merkle.daily_seed("2026-08-28", s)
    b = merkle.daily_seed("2026-08-28", s)
    assert a == b
    assert merkle.anchor(a, b"root") != a
