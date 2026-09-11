from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import (
    Extraction,
    Invoice,
    InvoiceMatch,
    InvoiceSource,
    PurchaseOrder,
    Vendor,
)
from invoice_ops.services.validation import run_validation
from invoice_ops.storage import get_storage

CLEAN_INVOICE = {
    "invoice": {
        "supplier_name": "ACME Supplies Ltd",
        "supplier_tax_id": "IT01234567890",
        "invoice_number": "INV-42",
        "invoice_date": "2026-01-15",
        "currency": "EUR",
        "purchase_order_number": "PO-5567",
        "line_items": [],
        "subtotal": "100.00",
        "tax_amount": "22.00",
        "total": "122.00",
    },
    "field_confidence": {},
    "notes": None,
}


def _seed_vendor_and_po(session, *, po_total="122.00") -> tuple[Vendor, PurchaseOrder]:
    vendor = Vendor(legal_name="ACME Supplies Ltd", tax_id="IT01234567890", aliases=[])
    session.add(vendor)
    session.flush()
    po = PurchaseOrder(
        po_number="PO-5567",
        vendor_id=vendor.id,
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("22.00"),
        total=Decimal(po_total),
        status="open",
    )
    session.add(po)
    session.flush()
    return vendor, po


def _seed_invoice_with_match(
    session, result_json: dict, vendor: Vendor, po: PurchaseOrder | None, *, suffix: str
) -> Invoice:
    data = f"fixture-{suffix}".encode()
    digest = hashlib.sha256(data).hexdigest()
    key = f"raw/{digest[:2]}/{digest}/doc.pdf"
    get_storage().put_object(key, data, "application/pdf")

    invoice = Invoice(
        status=InvoiceStatus.VALIDATING,
        source=InvoiceSource.UPLOAD,
        original_filename="doc.pdf",
        content_type="application/pdf",
        size_bytes=len(data),
        storage_key=key,
        content_sha256=digest,
    )
    session.add(invoice)
    session.flush()
    session.add(
        Extraction(
            invoice_id=invoice.id,
            model="fake/model",
            text_method="pdfplumber",
            attempts=1,
            ok=True,
            result_json=result_json,
            field_confidence={},
        )
    )
    session.add(
        InvoiceMatch(
            invoice_id=invoice.id,
            vendor_id=vendor.id,
            vendor_match_method="tax_id",
            vendor_confidence=1.0,
            vendor_candidates=[],
            po_id=po.id if po else None,
            po_match_method="exact_number" if po else "not_found",
        )
    )
    session.flush()
    session.refresh(invoice)
    return invoice


def test_clean_invoice_passes_and_advances_to_validated(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_invoice_with_match(db_session, CLEAN_INVOICE, vendor, po, suffix="a")

    validation = run_validation(db_session, invoice.id)

    assert validation is not None
    assert validation.passed is True
    assert invoice.status == InvoiceStatus.VALIDATED
    rule_names = {r["rule"] for r in validation.results}
    assert "po_amount_tolerance" in rule_names


def test_po_overage_fails_validation_but_still_advances(db_session):
    vendor, po = _seed_vendor_and_po(db_session, po_total="80.00")  # invoice is 122.00 -> way over
    invoice = _seed_invoice_with_match(db_session, CLEAN_INVOICE, vendor, po, suffix="b")

    validation = run_validation(db_session, invoice.id)

    assert validation.passed is False
    po_rule = next(r for r in validation.results if r["rule"] == "po_amount_tolerance")
    assert po_rule["severity"] == "error"
    assert not po_rule["passed"]
    # A failing rule set is a recorded outcome, not a pipeline failure -- the
    # approval decision (M5) is what acts on validation.passed.
    assert invoice.status == InvoiceStatus.VALIDATED
    assert invoice.failure_reason is None


def test_duplicate_invoice_number_detected_across_invoices(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    first = _seed_invoice_with_match(db_session, CLEAN_INVOICE, vendor, po, suffix="first")
    run_validation(db_session, first.id)  # first one through clean

    second = _seed_invoice_with_match(db_session, CLEAN_INVOICE, vendor, po, suffix="second")
    validation = run_validation(db_session, second.id)

    dup_rule = next(r for r in validation.results if r["rule"] == "duplicate_invoice_number")
    assert not dup_rule["passed"]
    assert dup_rule["severity"] == "error"
    assert str(first.id) in dup_rule["detail"]["candidate_ids"]


def test_non_validating_invoice_is_skipped(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_invoice_with_match(db_session, CLEAN_INVOICE, vendor, po, suffix="c")
    invoice.status = InvoiceStatus.EXTRACTED
    db_session.flush()

    assert run_validation(db_session, invoice.id) is None


def test_missing_invoice_raises(db_session):
    import uuid

    with pytest.raises(ValueError, match="not found"):
        run_validation(db_session, uuid.uuid4())


def test_task_chains_matching_into_validation(db_engine, monkeypatch):
    import uuid

    from invoice_ops.workers import tasks

    calls: list[str] = []

    def fake_match(session, invoice_id):
        calls.append("match")

        class _Stub:
            vendor_id = None
            vendor_match_method = "none"
            po_id = None
            po_match_method = "not_provided"

        return _Stub()

    def fake_validate(session, invoice_id):
        calls.append("validate")
        return None

    monkeypatch.setattr("invoice_ops.services.matching.run_matching", fake_match)
    monkeypatch.setattr("invoice_ops.services.validation.run_validation", fake_validate)

    tasks.match_invoice.run(str(uuid.uuid4()))

    assert calls == ["match", "validate"]
