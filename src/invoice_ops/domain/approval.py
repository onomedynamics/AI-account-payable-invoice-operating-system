"""The one place validation results + extraction confidence become an actual
decision: auto-approve, or send to a human.

Deliberately separate from the validation engine (M4) and deliberately
conservative. Every ``severity="error"`` rule passing tells you the numbers
are internally consistent -- it does not by itself mean it is safe to pay
without a human looking. This policy adds two further gates on top of that:

- was the vendor confidently identified (not "none"/"ambiguous")?
- did a PO actually get matched? ("no PO mismatch detected" is not the same
  as "a PO was found and checked" -- an invoice with no PO at all exercised
  no purchase-order control, regardless of what the arithmetic rules say.)

Auto-approval requires all of: clean validation, an identified vendor, a
matched PO, and high extraction confidence on every field that matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from invoice_ops.domain.extraction import CRITICAL_FIELDS
from invoice_ops.domain.state import InvoiceStatus

AUTO_APPROVE_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True)
class ApprovalDecision:
    outcome: InvoiceStatus  # AUTO_APPROVED | NEEDS_REVIEW
    reasons: tuple[str, ...] = field(default_factory=tuple)


def decide_approval(
    *,
    validation_passed: bool,
    vendor_identified: bool,
    has_po: bool,
    field_confidence: dict[str, float],
) -> ApprovalDecision:
    reasons: list[str] = []

    if not validation_passed:
        reasons.append("one or more validation rules failed")
    if not vendor_identified:
        reasons.append("vendor could not be confidently identified")
    if not has_po:
        reasons.append("no purchase order matched -- cannot confirm authorized spend")

    low_confidence = sorted(
        f
        for f in CRITICAL_FIELDS
        if field_confidence.get(f, 0.0) < AUTO_APPROVE_CONFIDENCE_THRESHOLD
    )
    if low_confidence:
        reasons.append(f"low extraction confidence on: {', '.join(low_confidence)}")

    outcome = InvoiceStatus.NEEDS_REVIEW if reasons else InvoiceStatus.AUTO_APPROVED
    return ApprovalDecision(outcome=outcome, reasons=tuple(reasons))
