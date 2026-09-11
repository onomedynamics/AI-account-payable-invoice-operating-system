from __future__ import annotations

import uuid
from decimal import Decimal

from invoice_ops.domain.extraction import ExtractedInvoice, LineItem
from invoice_ops.domain.validation import (
    DuplicateCandidate,
    PoSnapshot,
    ValidationInput,
    has_blocking_failure,
    rule_arithmetic_consistency,
    rule_duplicate_invoice_number,
    rule_line_items_sum_to_subtotal,
    rule_po_amount_tolerance,
    rule_po_currency_match,
    rule_po_vendor_mismatch,
    rule_required_fields_present,
    run_rules,
)


def _inv(**kw) -> ExtractedInvoice:
    return ExtractedInvoice(**kw)


def _input(**kw) -> ValidationInput:
    invoice = kw.pop("invoice", _inv())
    return ValidationInput(invoice=invoice, **kw)


def _po(
    po_number: str, currency: str, total: str, vendor_id: uuid.UUID | None = None
) -> PoSnapshot:
    return PoSnapshot(
        po_number=po_number,
        currency=currency,
        total=Decimal(total),
        vendor_id=vendor_id or uuid.uuid4(),
    )


# --- required_fields_present -------------------------------------------------


def test_required_fields_all_present():
    result = rule_required_fields_present(
        _input(
            invoice=_inv(
                supplier_name="ACME",
                invoice_number="INV-1",
                invoice_date="2026-01-01",
                total="10.00",
            )
        )
    )
    assert result.passed
    assert result.severity == "info"


def test_required_fields_missing_is_an_error():
    result = rule_required_fields_present(_input(invoice=_inv(supplier_name="ACME")))
    assert not result.passed
    assert result.severity == "error"
    assert "invoice_number" in result.detail["missing"]


# --- arithmetic_consistency ---------------------------------------------------


def test_arithmetic_consistency_passes_when_it_adds_up():
    result = rule_arithmetic_consistency(
        _input(invoice=_inv(subtotal="100.00", tax_amount="22.00", total="122.00"))
    )
    assert result.passed


def test_arithmetic_consistency_tolerates_rounding():
    result = rule_arithmetic_consistency(
        _input(invoice=_inv(subtotal="100.00", tax_amount="22.001", total="122.00"))
    )
    assert result.passed


def test_arithmetic_consistency_fails_on_real_mismatch():
    result = rule_arithmetic_consistency(
        _input(invoice=_inv(subtotal="100.00", tax_amount="22.00", total="200.00"))
    )
    assert not result.passed
    assert result.severity == "error"


def test_arithmetic_consistency_skips_when_incomplete():
    result = rule_arithmetic_consistency(_input(invoice=_inv(total="122.00")))
    assert result.passed
    assert result.severity == "info"
    assert "skipped" in result.message


# --- line_items_sum_to_subtotal -----------------------------------------------


def test_line_items_sum_matches_subtotal():
    items = [
        LineItem(description="a", line_total="50.00"),
        LineItem(description="b", line_total="50.00"),
    ]
    result = rule_line_items_sum_to_subtotal(
        _input(invoice=_inv(line_items=items, subtotal="100.00"))
    )
    assert result.passed


def test_line_items_sum_mismatch_is_a_warning_not_blocking():
    # Downgraded from "error": real invoices routinely carry small ancillary
    # charges not captured as line items, so this fires on most genuine
    # invoices and must not by itself block auto-approval. See the comment
    # on the rule.
    items = [LineItem(description="a", line_total="50.00")]
    result = rule_line_items_sum_to_subtotal(
        _input(invoice=_inv(line_items=items, subtotal="100.00"))
    )
    assert not result.passed
    assert result.severity == "warning"


def test_line_items_with_missing_line_total_is_a_warning_not_silent():
    items = [LineItem(description="a", line_total="50.00"), LineItem(description="b")]
    result = rule_line_items_sum_to_subtotal(
        _input(invoice=_inv(line_items=items, subtotal="100.00"))
    )
    assert not result.passed
    assert result.severity == "warning"


def test_line_items_skip_when_none_extracted():
    result = rule_line_items_sum_to_subtotal(_input(invoice=_inv(subtotal="100.00")))
    assert result.passed
    assert result.severity == "info"


# --- po_amount_tolerance (the headline rule) ----------------------------------


def test_po_amount_within_tolerance_passes():
    po = _po("PO-1", "EUR", "4200000")
    result = rule_po_amount_tolerance(
        _input(invoice=_inv(total="4250000"), matched_po=po)  # ~1.2% over
    )
    assert result.passed


def test_po_amount_far_over_tolerance_blocks_with_clear_message():
    # The example from the brief: invoice 4,850,000 vs PO 4,200,000 (~15.5% over).
    po = _po("PO-77", "NGN", "4200000")
    result = rule_po_amount_tolerance(_input(invoice=_inv(total="4850000"), matched_po=po))
    assert not result.passed
    assert result.severity == "error"
    assert "exceeds" in result.message
    assert "PO-77" in result.message
    pct = float(result.detail["pct_diff"])
    assert 0.15 < pct < 0.16  # 650,000 / 4,200,000 ~= 15.48%


def test_po_amount_skipped_without_a_matched_po():
    result = rule_po_amount_tolerance(_input(invoice=_inv(total="100.00"), matched_po=None))
    assert result.passed
    assert result.severity == "info"


def test_po_amount_missing_invoice_total_is_a_warning():
    po = _po("PO-1", "EUR", "100.00")
    result = rule_po_amount_tolerance(_input(invoice=_inv(), matched_po=po))
    assert not result.passed
    assert result.severity == "warning"


# --- po_currency_match ---------------------------------------------------------


def test_currency_match_passes():
    po = _po("PO-1", "eur", "1")
    result = rule_po_currency_match(_input(invoice=_inv(currency="EUR"), matched_po=po))
    assert result.passed


def test_currency_mismatch_is_an_error():
    po = _po("PO-1", "USD", "1")
    result = rule_po_currency_match(_input(invoice=_inv(currency="EUR"), matched_po=po))
    assert not result.passed
    assert result.severity == "error"


# --- po_vendor_mismatch ---------------------------------------------------------


def test_po_vendor_mismatch_skipped_without_a_po():
    result = rule_po_vendor_mismatch(_input())
    assert result.passed
    assert result.severity == "info"


def test_po_vendor_mismatch_passes_when_ids_agree():
    vendor_id = uuid.uuid4()
    po = _po("PO-1", "EUR", "1", vendor_id)
    result = rule_po_vendor_mismatch(_input(matched_po=po, matched_vendor_id=vendor_id))
    assert result.passed


def test_po_vendor_mismatch_is_an_error_when_ids_disagree():
    po = _po("PO-1", "EUR", "1")
    result = rule_po_vendor_mismatch(_input(matched_po=po, matched_vendor_id=uuid.uuid4()))
    assert not result.passed
    assert result.severity == "error"


def test_po_vendor_mismatch_warns_when_invoice_vendor_unknown():
    po = _po("PO-1", "EUR", "1")
    result = rule_po_vendor_mismatch(_input(matched_po=po, matched_vendor_id=None))
    assert not result.passed
    assert result.severity == "warning"


# --- duplicate_invoice_number ---------------------------------------------------


def test_no_duplicate_candidates_passes():
    result = rule_duplicate_invoice_number(_input(invoice=_inv(invoice_number="INV-1")))
    assert result.passed


def test_duplicate_candidates_present_is_an_error():
    candidate = DuplicateCandidate(
        invoice_id=uuid.uuid4(), invoice_number="INV-1", status="approved"
    )
    result = rule_duplicate_invoice_number(
        _input(invoice=_inv(invoice_number="INV-1"), duplicate_candidates=(candidate,))
    )
    assert not result.passed
    assert result.severity == "error"


def test_duplicate_check_skips_without_an_invoice_number():
    candidate = DuplicateCandidate(
        invoice_id=uuid.uuid4(), invoice_number="INV-1", status="approved"
    )
    result = rule_duplicate_invoice_number(
        _input(invoice=_inv(), duplicate_candidates=(candidate,))
    )
    assert result.passed
    assert result.severity == "info"


# --- run_rules / has_blocking_failure -------------------------------------------


def test_run_rules_returns_one_result_per_rule():
    results = run_rules(_input())
    assert len(results) == 7


def test_has_blocking_failure_true_when_any_error_fails():
    results = run_rules(_input(invoice=_inv(subtotal="10.00", tax_amount="1.00", total="999.00")))
    assert has_blocking_failure(results)


def test_has_blocking_failure_false_on_clean_invoice():
    results = run_rules(
        _input(
            invoice=_inv(
                supplier_name="ACME",
                invoice_number="INV-1",
                invoice_date="2026-01-01",
                currency="EUR",
                subtotal="100.00",
                tax_amount="22.00",
                total="122.00",
            )
        )
    )
    assert not has_blocking_failure(results)
