"""§10 canonicaliser tests (IMPLEMENTATION_SPEC §1.1)."""

from openstore.canonical import canonical_json_bytes, digest
from openstore.evidence import canonical_json_bytes as evidence_canonical


def test_canonical_json_vectors():
    # sorted keys, tight separators, no spaces
    assert canonical_json_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    # nested + list ordering
    assert canonical_json_bytes({"a": [3, 1, 2], "z": {"y": 0}}) == b'{"a":[3,1,2],"z":{"y":0}}'
    # NFC normalization collapses equivalent compositions
    assert canonical_json_bytes({"s": "é"}) == canonical_json_bytes({"s": "e\u0301"})
    # integer-only; float rejected
    try:
        canonical_json_bytes({"x": 1.5})
    except Exception:
        pass
    else:
        raise AssertionError("float should be rejected")


def test_merchant_and_verifier_canonicalisers_agree():
    # The verifier re-uses the same canonicaliser the issuer uses.
    obj = {"sku": "A", "qty": 2, "tags": ["x", "y"], "nested": {"k": [1, 2]}}
    assert canonical_json_bytes(obj) == evidence_canonical(obj)
    # and the digest is stable
    assert digest(obj) == "sha256:" + __import__("hashlib").sha256(canonical_json_bytes(obj)).hexdigest()
