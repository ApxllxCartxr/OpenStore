# tests/stage04/test_canonical.py
# S4.1: verify canonical JSON vectors byte-for-byte.

from __future__ import annotations

import json
from pathlib import Path

from openstore.core.poai import canonical_json_bytes

GOLDEN = Path(__file__).resolve().parent.parent / "GOLDEN" / "canonical"


def _load(name: str) -> dict:
    with open(GOLDEN / f"{name}.json") as f:
        return json.load(f)


class TestCanonicalJSON:
    def test_simple_object_sorted_keys(self):
        v = _load("simple_object")
        computed = canonical_json_bytes({"b": 2, "a": 1})
        assert computed == bytes.fromhex(v["canonical_hex"])

    def test_nested_object_sorted_keys(self):
        v = _load("nested_object")
        computed = canonical_json_bytes({"outer": {"z": 1, "a": 2}, "x": 0})
        assert computed == bytes.fromhex(v["canonical_hex"])

    def test_unicode_value(self):
        v = _load("unicode_value")
        computed = canonical_json_bytes({"name": "\u0930\u093e\u091c", "qty": 5})
        assert computed == bytes.fromhex(v["canonical_hex"])

    def test_empty_list(self):
        v = _load("empty_list")
        computed = canonical_json_bytes({"items": []})
        assert computed == bytes.fromhex(v["canonical_hex"])

    def test_empty_object(self):
        v = _load("empty_object")
        computed = canonical_json_bytes({"metadata": {}})
        assert computed == bytes.fromhex(v["canonical_hex"])

    def test_null_section(self):
        v = _load("null_section")
        computed = canonical_json_bytes(None)
        assert computed == bytes.fromhex(v["canonical_hex"])

    def test_integer_no_quotes(self):
        v = _load("integer_no_quotes")
        computed = canonical_json_bytes({"amount": 21000, "qty": 1})
        assert computed == bytes.fromhex(v["canonical_hex"])
        text = computed.decode("utf-8")
        assert '"21000"' not in text
        assert "21000" in text

    def test_mixed_types(self):
        v = _load("mixed_types")
        computed = canonical_json_bytes(
            {
                "sku": "GEL-VAN-500",
                "price_minor": 21000,
                "tags": ["vegan", "dairy-free"],
                "catalog_digest": "sha256:abc123",
                "merchant_id": "gelateria-roma",
                "iat": 1787000000,
            }
        )
        assert computed == bytes.fromhex(v["canonical_hex"])

    def test_null_is_bnull_not_absent(self):
        data = {"section": None}
        computed = canonical_json_bytes(data)
        assert computed == b'{"section":null}'

    def test_duplicate_keys_not_present(self):
        computed = canonical_json_bytes({"a": 1, "b": 2})
        parsed = json.loads(computed)
        assert set(parsed.keys()) == {"a", "b"}
        assert len(parsed) == 2
