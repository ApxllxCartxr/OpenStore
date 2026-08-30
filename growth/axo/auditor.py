"""AXO auditor — AUDIT step (GROWTH_AGENTS.md §5.1, R5.2d).

Reads each product's name/description/tags and proposes tag additions. Every
proposed tag MUST cite the span of product text supporting it (R5.2d) — a tag
with no textual evidence is never proposed, because tags are authorization
inputs and a hallucinated tag is a security defect (§5.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..catalog import Product

# keyword -> candidate tag, with regexes that yield a quotable evidence span.
_TAG_PATTERNS: Dict[str, List[str]] = {
    "vegan": [r"vegan", r"plant-?based", r"no animal", r"coconut-milk", r"oat-milk"],
    "dairy-free": [r"dairy-?free", r"non-?dairy", r"no dairy", r"coconut-milk", r"oat-milk"],
    "gluten-free": [r"gluten-?free", r"no gluten"],
    "nut-free": [r"nut-?free", r"no nuts", r"no nut"],
    "nut": [r"pistachio", r"hazelnut", r"almond", r"pecan", r"contains nuts", r"with nuts"],
    "alcohol": [r"alcohol", r"\brum\b", r"\bwine\b", r"vodka", r"whisky"],
}

# Explicit self-declared tags are high confidence; inferred ones are lower.
_EXPLICIT = {"vegan", "dairy-free", "gluten-free", "nut-free"}


@dataclass(frozen=True, slots=True)
class TagProposal:
    sku: str
    add_tag: str
    evidence: str
    confidence: float


@dataclass(slots=True)
class AuditReport:
    tag_proposals: List[TagProposal] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _scan(text: str, pattern: str):
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(0) if m else None


def audit_product(product: Product) -> List[TagProposal]:
    proposals: List[TagProposal] = []
    text = f"{product.title}. {product.description}"
    have = set(product.tags)
    for tag, patterns in _TAG_PATTERNS.items():
        if tag in have:
            continue
        for pat in patterns:
            span = _scan(text, pat)
            if span:
                confidence = 0.92 if tag in _EXPLICIT else 0.7
                proposals.append(TagProposal(
                    sku=product.sku, add_tag=tag,
                    evidence=span, confidence=confidence))
                break
    return proposals


def audit_catalog(catalog: Dict[str, Product]) -> AuditReport:
    report = AuditReport()
    for p in catalog.values():
        report.tag_proposals.extend(audit_product(p))
        if not p.related_skus:
            report.notes.append(f"{p.sku}: missing related_skus")
    return report
