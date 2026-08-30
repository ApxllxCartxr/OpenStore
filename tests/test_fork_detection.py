"""Cross-bundle fork detection (DELEGATION §4) — a single-bundle verifier can't
catch a fork; it only surfaces when two bundles claim the same (envelope, sequence)
with different signatures. `detect_forks` / `scan_bundles_for_forks` is the batch check."""

from openstore.verify import scan_bundles_for_forks


def _bundle(bid, envelope_id, sequence, signature):
    return {
        "bundle_id": bid,
        "authority": {
            "delegation": {
                "spend_chain": [
                    {
                        "envelope_id": envelope_id,
                        "sequence": sequence,
                        "entry_type": "SPEND",
                        "amount_minor": 10000,
                        "ref": bid,
                        "prev_link": "sha256:abc",
                        "issued_at": "2026-09-01T00:00:00Z",
                        "signature": signature,
                    }
                ]
            }
        },
    }


def test_fork_detected_when_same_envelope_sequence_diverges():
    b1 = _bundle("poai-A1", "env_B", 0, "sig-merchant-1")
    b2 = _bundle("poai-A2", "env_B", 0, "sig-merchant-2")
    proofs = scan_bundles_for_forks([b1, b2])
    assert len(proofs) == 1
    assert proofs[0]["reason"] == "fork_detected"
    assert proofs[0]["envelope_id"] == "env_B"
    assert proofs[0]["sequence"] == 0
    assert {r["bundle_id"] for r in proofs[0]["entries"]} == {"poai-A1", "poai-A2"}


def test_no_fork_when_sequences_distinct():
    b1 = _bundle("poai-A1", "env_B", 0, "sig-1")
    b2 = _bundle("poai-A2", "env_B", 1, "sig-2")
    assert scan_bundles_for_forks([b1, b2]) == []


def test_no_fork_when_same_signature_replayed():
    # Same signature (e.g. the same merchant re-presenting its own entry) is not a fork.
    b1 = _bundle("poai-A1", "env_B", 0, "sig-shared")
    b2 = _bundle("poai-A2", "env_B", 0, "sig-shared")
    assert scan_bundles_for_forks([b1, b2]) == []
