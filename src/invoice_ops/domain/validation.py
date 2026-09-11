"""Deterministic financial rules. No LLM, no I/O -- every rule is a pure
function from already-fetched data to a :class:`RuleResult`.

This is the module the project's whole thesis rests on: AI (extraction)
produced the numbers; these rules decide whether they add up. A rule never
guesses either -- missing data it needs to check something is a ``skip``
(``info`` severity, ``passed=True``), not a silent pass and not a failure.

``severity="error"`` marks a rule whose failure should block auto-approval.
That gate is enforced by the approval policy (M5), not here; this module only
produces the data it needs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from invoice_ops.domain.extraction import ExtractedInvoice

# Rounding slack for sum/arithmetic checks -- never for the PO tolerance check,
# which is a percentage by design.
AMOUNT_TOLERANCE = Decimal("0.02")

# How far an invoice total may drift from its matched PO's total before the
# "exceeds/under PO" rule fails. A real deployment would likely combine a
# percentage with an absolute floor (e.g. "greater of 2% or $50"); kept as a
# flat percentage here and left as an obvious M5 policy knob.
PO_TOLERANCE_PCT = Decimal("0.02")

REQUIRED_FIELDS: tuple[str, ...] = ("supplier_name", "invoice_number", "invoice_date", "total")


@dataclass(frozen=True)
class RuleResult:
    rule: str
    severity: str  # "info" | "warning" | "error"
    passed: bool
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PoSnapshot:
    po_number: str
    currency: str
    total: Decimal
    vendor_id: uuid.UUID


@dataclass(frozen=True)
class DuplicateCandidate:
    invoice_id: uuid.UUID
    invoice_number: str
    status: str


@dataclass(frozen=True)
class ValidationInput:
    invoice: ExtractedInvoice
    matched_po: PoSnapshot | None = None
    # The vendor identified from the extracted supplier name/tax_id (M3), which
    # can differ from matched_po.vendor_id -- a PO number that resolves under a
    # *different* vendor than the invoice's own supplier is a real anomaly.
    matched_vendor_id: uuid.UUID | None = None
    # Other invoices already found (by the service layer) with the same
    # vendor and the same invoice_number. Non-empty means a likely duplicate.
    duplicate_candidates: tuple[DuplicateCandidate, ...] = ()


def rule_required_fields_present(inp: ValidationInput) -> RuleResult:
    missing = [f for f in REQUIRED_FIELDS if getattr(inp.invoice, f) is None]
    passed = not missing
    return RuleResult(
        rule="required_fields_present",
        severity="info" if passed else "error",
        passed=passed,
        message="all required fields present"
        if passed
        else f"missing required field(s): {', '.join(missing)}",
        detail={"missing": missing},
    )


def rule_arithmetic_consistency(inp: ValidationInput) -> RuleResult:
    subtotal, tax, total = inp.invoice.subtotal, inp.invoice.tax_amount, inp.invoice.total
    if subtotal is None or tax is None or total is None:
        return RuleResult(
            rule="arithmetic_consistency",
            severity="info",
            passed=True,
            message="skipped: subtotal, tax_amount and total are not all present",
        )
    expected = subtotal + tax
    diff = abs(expected - total)
    passed = diff <= AMOUNT_TOLERANCE
    return RuleResult(
        rule="arithmetic_consistency",
        severity="info" if passed else "error",
        passed=passed,
        message="subtotal + tax matches total"
        if passed
        else f"subtotal ({subtotal}) + tax ({tax}) = {expected}, but total is {total}",
        detail={"subtotal": str(subtotal), "tax_amount": str(tax), "total": str(total)},
    )


def rule_line_items_sum_to_subtotal(inp: ValidationInput) -> RuleResult:
    items = inp.invoice.line_items
    subtotal = inp.invoice.subtotal
    if not items or subtotal is None:
        return RuleResult(
            rule="line_items_sum_to_subtotal",
            severity="info",
            passed=True,
            message="skipped: no line items or no subtotal to check against",
        )
    priced = [li.line_total for li in items if li.line_total is not None]
    if len(priced) != len(items):
        return RuleResult(
            rule="line_items_sum_to_subtotal",
            severity="warning",
            passed=False,
            message=f"{len(items) - len(priced)} of {len(items)} line item(s) have no line_total",
        )
    total_lines = sum(priced, Decimal("0"))
    diff = abs(total_lines - subtotal)
    passed = diff <= AMOUNT_TOLERANCE
    return RuleResult(
        rule="line_items_sum_to_subtotal",
        severity="info" if passed else "error",
        passed=passed,
        message="line items sum to the subtotal"
        if passed
        else f"line items sum to {total_lines}, but subtotal is {subtotal}",
        detail={"line_items_total": str(total_lines), "subtotal": str(subtotal)},
    )


def rule_po_amount_tolerance(inp: ValidationInput) -> RuleResult:
    if inp.matched_po is None:
        return RuleResult(
            rule="po_amount_tolerance",
            severity="info",
            passed=True,
            message="skipped: no PO matched",
        )
    if inp.invoice.total is None:
        return RuleResult(
            rule="po_amount_tolerance",
            severity="warning",
            passed=False,
            message="invoice total is missing, cannot compare to the PO",
        )
    po_total = inp.matched_po.total
    invoice_total = inp.invoice.total
    if po_total == 0:
        return RuleResult(
            rule="po_amount_tolerance",
            severity="warning",
            passed=False,
            message=f"PO {inp.matched_po.po_number} total is zero, cannot compute a tolerance",
        )
    diff = invoice_total - po_total
    pct = abs(diff) / po_total
    passed = pct <= PO_TOLERANCE_PCT
    direction = "exceeds" if diff > 0 else "is under"
    message = (
        f"invoice total within tolerance of PO {inp.matched_po.po_number}"
        if passed
        else (
            f"invoice {direction} PO {inp.matched_po.po_number} value by "
            f"{abs(diff)} ({pct:.1%}). Payment blocked pending review."
        )
    )
    return RuleResult(
        rule="po_amount_tolerance",
        severity="info" if passed else "error",
        passed=passed,
        message=message,
        detail={
            "invoice_total": str(invoice_total),
            "po_total": str(po_total),
            "pct_diff": str(pct),
        },
    )


def rule_po_currency_match(inp: ValidationInput) -> RuleResult:
    if inp.matched_po is None:
        return RuleResult(
            rule="po_currency_match", severity="info", passed=True, message="skipped: no PO matched"
        )
    if not inp.invoice.currency:
        return RuleResult(
            rule="po_currency_match",
            severity="warning",
            passed=False,
            message="invoice currency is missing",
        )
    passed = inp.invoice.currency.strip().upper() == inp.matched_po.currency.strip().upper()
    return RuleResult(
        rule="po_currency_match",
        severity="info" if passed else "error",
        passed=passed,
        message="currency matches the PO"
        if passed
        else f"invoice currency {inp.invoice.currency!r} does not match PO currency "
        f"{inp.matched_po.currency!r}",
    )


def rule_po_vendor_mismatch(inp: ValidationInput) -> RuleResult:
    if inp.matched_po is None:
        return RuleResult(
            rule="po_vendor_mismatch",
            severity="info",
            passed=True,
            message="skipped: no PO matched",
        )
    if inp.matched_vendor_id is None:
        return RuleResult(
            rule="po_vendor_mismatch",
            severity="warning",
            passed=False,
            message=f"PO {inp.matched_po.po_number} found, but the invoice's own supplier "
            "could not be identified to cross-check it against",
        )
    passed = inp.matched_po.vendor_id == inp.matched_vendor_id
    return RuleResult(
        rule="po_vendor_mismatch",
        severity="info" if passed else "error",
        passed=passed,
        message="PO belongs to the matched vendor"
        if passed
        else f"PO {inp.matched_po.po_number} belongs to a different vendor than the invoice's "
        "own extracted supplier",
    )


def rule_duplicate_invoice_number(inp: ValidationInput) -> RuleResult:
    if not inp.invoice.invoice_number:
        return RuleResult(
            rule="duplicate_invoice_number",
            severity="info",
            passed=True,
            message="skipped: no invoice number extracted",
        )
    if not inp.duplicate_candidates:
        return RuleResult(
            rule="duplicate_invoice_number",
            severity="info",
            passed=True,
            message="no other invoice found with the same vendor and invoice number",
        )
    ids = [str(c.invoice_id) for c in inp.duplicate_candidates]
    return RuleResult(
        rule="duplicate_invoice_number",
        severity="error",
        passed=False,
        message=f"invoice number {inp.invoice.invoice_number!r} already exists on: "
        f"{', '.join(ids)}",
        detail={"candidate_ids": ids},
    )


RULES: tuple[Callable[[ValidationInput], RuleResult], ...] = (
    rule_required_fields_present,
    rule_arithmetic_consistency,
    rule_line_items_sum_to_subtotal,
    rule_po_amount_tolerance,
    rule_po_currency_match,
    rule_po_vendor_mismatch,
    rule_duplicate_invoice_number,
)


def run_rules(inp: ValidationInput) -> list[RuleResult]:
    return [rule(inp) for rule in RULES]


def has_blocking_failure(results: Sequence[RuleResult]) -> bool:
    return any(r.severity == "error" and not r.passed for r in results)
