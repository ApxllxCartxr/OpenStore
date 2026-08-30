import hashlib
from typing import Dict, List, Tuple

from .cbor import sha256


def daily_seed(date: str, secret: bytes) -> bytes:
    return sha256(f"{date}|".encode() + secret)


def anchor(seed: bytes, root: bytes) -> bytes:
    return sha256(seed + root)


def merkle_leaf(receipt_id: str) -> bytes:
    return sha256(receipt_id.encode("utf-8"))


def _hash_pair(a: bytes, b: bytes) -> bytes:
    return sha256(a + b)


def build_tree(leaves: List[bytes]) -> Tuple[bytes, Dict[str, List[Tuple[bool, bytes]]]]:
    sorted_leaves = sorted(leaves)
    levels = [sorted_leaves]
    while len(levels[-1]) > 1:
        prev = levels[-1]
        nxt = []
        for i in range(0, len(prev), 2):
            left = prev[i]
            right = prev[i + 1] if i + 1 < len(prev) else prev[i]
            nxt.append(_hash_pair(left, right))
        levels.append(nxt)
    root = levels[-1][0]
    proofs: Dict[str, List[Tuple[bool, bytes]]] = {}
    for idx, leaf in enumerate(sorted_leaves):
        proof = []
        cur = idx
        for level in levels[:-1]:
            sib = cur ^ 1
            sibling_is_left = cur % 2 == 1
            sib_hash = level[sib] if sib < len(level) else level[cur]
            proof.append((sibling_is_left, sib_hash))
            cur //= 2
        proofs[leaf.hex()] = proof
    return root, proofs


def verify_inclusion(receipt_id: str, root: bytes, proof: List[Tuple[bool, bytes]]) -> bool:
    cur = merkle_leaf(receipt_id)
    for sibling_is_left, sib in proof:
        cur = _hash_pair(sib, cur) if sibling_is_left else _hash_pair(cur, sib)
    return cur == root
