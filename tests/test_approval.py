from __future__ import annotations

from invoice_ops.domain.approval import decide_approval
from invoice_ops.domain.extraction import CRITICAL_FIELDS
from invoice_ops.domain.state import InvoiceStatus

GOOD_CONFIDENCE = {f: 0.95 for f in CRITICAL_FIELDS}


def test_everything_clean_auto_approves():
    decision = decide_approval(
        validation_passed=True,
        vendor_identified=True,
        has_po=True,
        field_confidence=GOOD_CONFIDENCE,
    )
    assert decision.outcome == InvoiceStatus.AUTO_APPROVED
    assert decision.reasons == ()


def test_failed_validation_forces_review():
    decision = decide_approval(
        validation_passed=False,
        vendor_identified=True,
        has_po=True,
        field_confidence=GOOD_CONFIDENCE,
    )
    assert decision.outcome == InvoiceStatus.NEEDS_REVIEW
    assert any("validation" in r for r in decision.reasons)


def test_unidentified_vendor_forces_review():
    decision = decide_approval(
        validation_passed=True,
        vendor_identified=False,
        has_po=True,
        field_confidence=GOOD_CONFIDENCE,
    )
    assert decision.outcome == InvoiceStatus.NEEDS_REVIEW
    assert any("vendor" in r for r in decision.reasons)


def test_missing_po_forces_review_even_if_rules_passed():
    # A validation pass with no PO matched is a *vacuous* pass (the PO rules
    # all skip) -- the policy must not treat that as safe to auto-approve.
    decision = decide_approval(
        validation_passed=True,
        vendor_identified=True,
        has_po=False,
        field_confidence=GOOD_CONFIDENCE,
    )
    assert decision.outcome == InvoiceStatus.NEEDS_REVIEW
    assert any("purchase order" in r for r in decision.reasons)


def test_low_confidence_on_a_critical_field_forces_review():
    confidence = dict(GOOD_CONFIDENCE)
    confidence["total"] = 0.4
    decision = decide_approval(
        validation_passed=True, vendor_identified=True, has_po=True, field_confidence=confidence
    )
    assert decision.outcome == InvoiceStatus.NEEDS_REVIEW
    assert any("total" in r for r in decision.reasons)


def test_missing_confidence_entry_counts_as_low_confidence():
    confidence = {f: 0.95 for f in CRITICAL_FIELDS if f != "total"}
    decision = decide_approval(
        validation_passed=True, vendor_identified=True, has_po=True, field_confidence=confidence
    )
    assert decision.outcome == InvoiceStatus.NEEDS_REVIEW


def test_multiple_problems_are_all_reported():
    decision = decide_approval(
        validation_passed=False, vendor_identified=False, has_po=False, field_confidence={}
    )
    assert decision.outcome == InvoiceStatus.NEEDS_REVIEW
    assert len(decision.reasons) >= 3
