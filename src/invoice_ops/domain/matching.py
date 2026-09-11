"""Deterministic vendor + PO matching.

Same priority-chain shape as everything else in this codebase: an ordered list
of increasingly fuzzy checks, the first unambiguous hit wins, and anything
short of unambiguous is *recorded*, not guessed. No LLM involved -- identity
resolution here is exact-string and edit-distance matching, which is a solved
problem that does not need a model.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from invoice_ops.domain.extraction import ExtractedInvoice

# A fuzzy hit below this similarity isn't even worth reporting as a candidate.
FUZZY_FLOOR = 0.60
# A fuzzy hit at or above this, with a clear lead over the runner-up, auto-accepts.
FUZZY_AUTO_ACCEPT = 0.88
# "Clear lead" = at least this much ahead of the second-best candidate.
FUZZY_AMBIGUITY_GAP = 0.08


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.strip().lower().split())


def _normalize_code(value: str | None) -> str:
    """For tax IDs and PO numbers: strip everything but letters/digits, upper-case."""
    if not value:
        return ""
    return "".join(ch for ch in value.upper() if ch.isalnum())


@dataclass(frozen=True)
class VendorIdentity:
    vendor_id: uuid.UUID
    legal_name: str
    tax_id: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class VendorCandidate:
    vendor_id: uuid.UUID
    legal_name: str
    score: float


@dataclass(frozen=True)
class VendorMatchResult:
    vendor_id: uuid.UUID | None
    method: str  # "tax_id" | "exact_name" | "fuzzy_name" | "ambiguous" | "none"
    confidence: float
    candidates: tuple[VendorCandidate, ...] = field(default_factory=tuple)


def match_vendor(
    extracted: ExtractedInvoice, vendors: Sequence[VendorIdentity]
) -> VendorMatchResult:
    tax_id = _normalize_code(extracted.supplier_tax_id)
    if tax_id:
        hits = [v for v in vendors if _normalize_code(v.tax_id) == tax_id]
        if len(hits) == 1:
            return VendorMatchResult(vendor_id=hits[0].vendor_id, method="tax_id", confidence=1.0)

    name = _normalize_text(extracted.supplier_name)
    if not name:
        return VendorMatchResult(vendor_id=None, method="none", confidence=0.0)

    exact_hits = [
        v
        for v in vendors
        if name == _normalize_text(v.legal_name) or name in {_normalize_text(a) for a in v.aliases}
    ]
    if len(exact_hits) == 1:
        return VendorMatchResult(
            vendor_id=exact_hits[0].vendor_id, method="exact_name", confidence=0.95
        )

    scored: list[VendorCandidate] = []
    for v in vendors:
        best = max(
            (
                SequenceMatcher(None, name, _normalize_text(n)).ratio()
                for n in (v.legal_name, *v.aliases)
            ),
            default=0.0,
        )
        if best >= FUZZY_FLOOR:
            scored.append(VendorCandidate(v.vendor_id, v.legal_name, best))
    scored.sort(key=lambda c: c.score, reverse=True)

    if not scored:
        return VendorMatchResult(vendor_id=None, method="none", confidence=0.0)

    top = scored[0]
    runner_up = scored[1].score if len(scored) > 1 else 0.0
    if top.score >= FUZZY_AUTO_ACCEPT and (top.score - runner_up) >= FUZZY_AMBIGUITY_GAP:
        return VendorMatchResult(
            vendor_id=top.vendor_id,
            method="fuzzy_name",
            confidence=top.score,
            candidates=tuple(scored),
        )
    return VendorMatchResult(
        vendor_id=None, method="ambiguous", confidence=top.score, candidates=tuple(scored)
    )


@dataclass(frozen=True)
class PoIdentity:
    po_id: uuid.UUID
    po_number: str


@dataclass(frozen=True)
class PoMatchResult:
    po_id: uuid.UUID | None
    method: str  # "exact_number" | "not_found" | "not_provided" | "ambiguous"


def match_po(extracted: ExtractedInvoice, purchase_orders: Sequence[PoIdentity]) -> PoMatchResult:
    number = _normalize_code(extracted.purchase_order_number)
    if not number:
        return PoMatchResult(po_id=None, method="not_provided")

    hits = [po for po in purchase_orders if _normalize_code(po.po_number) == number]
    if len(hits) == 1:
        return PoMatchResult(po_id=hits[0].po_id, method="exact_number")
    if len(hits) > 1:
        return PoMatchResult(po_id=None, method="ambiguous")
    return PoMatchResult(po_id=None, method="not_found")
