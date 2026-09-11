from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Extraction, Invoice, InvoiceSource, PoLine, PurchaseOrder, Vendor
from invoice_ops.services.matching import run_matching
from invoice_ops.storage import get_storage

GOOD_INVOICE_JSON = {
    "invoice": {
        "supplier_name": "ACME Supplies Ltd",
        "supplier_tax_id": "IT 01234567890",
        "invoice_number": "INV-42",
        "invoice_date": "2026-01-15",
        "currency": "EUR",
        "purchase_order_number": "po-5567",
        "line_items": [],
        "subtotal": "100.00",
        "tax_amount": "22.00",
        "total": "122.00",
    },
    "field_confidence": {"supplier_name": 0.95, "total": 0.9},
    "notes": None,
}

UNKNOWN_INVOICE_JSON = {
    "invoice": {
        "supplier_name": "Some Random Company Nobody Knows",
        "invoice_number": "INV-9",
        "currency": "EUR",
        "purchase_order_number": "PO-0000",
        "line_items": [],
        "total": "5.00",
    },
    "field_confidence": {},
    "notes": None,
}


def _seed_vendor_and_po(session) -> Vendor:
    vendor = Vendor(
        legal_name="ACME Supplies Ltd",
        tax_id="IT01234567890",
        aliases=["ACME"],
        country="IT",
    )
    session.add(vendor)
    session.flush()

    po = PurchaseOrder(
        po_number="PO-5567",
        vendor_id=vendor.id,
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("22.00"),
        total=Decimal("122.00"),
        status="open",
    )
    session.add(po)
    session.flush()
    session.add(
        PoLine(
            po_id=po.id,
            description="Widget",
            quantity=Decimal("10"),
            unit_price=Decimal("10.00"),
            line_total=Decimal("100.00"),
        )
    )
    session.flush()
    return vendor


def _seed_extracted_invoice(
    session, result_json: dict, *, status=InvoiceStatus.EXTRACTED
) -> Invoice:
    data = f"fixture-{result_json['invoice']['invoice_number']}".encode()
    digest = hashlib.sha256(data).hexdigest()
    key = f"raw/{digest[:2]}/{digest}/doc.pdf"
    get_storage().put_object(key, data, "application/pdf")

    invoice = Invoice(
        status=status,
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
            field_confidence=result_json["field_confidence"],
        )
    )
    session.flush()
    session.refresh(invoice)
    return invoice


def test_matches_by_tax_id_and_po_and_advances_to_validating(db_session):
    _seed_vendor_and_po(db_session)
    invoice = _seed_extracted_invoice(db_session, GOOD_INVOICE_JSON)

    match = run_matching(db_session, invoice.id)

    assert match is not None
    assert match.vendor_match_method == "tax_id"
    assert match.vendor_confidence == 1.0
    assert match.vendor_id is not None
    assert match.po_match_method == "exact_number"
    assert match.po_id is not None
    assert invoice.status == InvoiceStatus.VALIDATING


def test_unknown_vendor_and_po_are_recorded_not_failed(db_session):
    _seed_vendor_and_po(db_session)  # exists, but nothing in the invoice matches it
    invoice = _seed_extracted_invoice(db_session, UNKNOWN_INVOICE_JSON)

    match = run_matching(db_session, invoice.id)

    assert match is not None
    assert match.vendor_id is None
    assert match.vendor_match_method == "none"
    assert match.po_id is None
    assert match.po_match_method == "not_found"
    # Uncertainty is recorded, not a pipeline failure.
    assert invoice.status == InvoiceStatus.VALIDATING
    assert invoice.failure_reason is None


def test_no_po_number_on_invoice_is_not_provided(db_session):
    _seed_vendor_and_po(db_session)
    no_po_json = {
        **GOOD_INVOICE_JSON,
        "invoice": {**GOOD_INVOICE_JSON["invoice"], "purchase_order_number": None},
    }
    invoice = _seed_extracted_invoice(db_session, no_po_json)

    match = run_matching(db_session, invoice.id)

    assert match.vendor_match_method == "tax_id"  # vendor still found
    assert match.po_id is None
    assert match.po_match_method == "not_provided"


def test_non_extracted_invoice_is_skipped(db_session):
    invoice = _seed_extracted_invoice(db_session, GOOD_INVOICE_JSON, status=InvoiceStatus.RECEIVED)
    assert run_matching(db_session, invoice.id) is None


def test_missing_invoice_raises(db_session):
    import uuid

    with pytest.raises(ValueError, match="not found"):
        run_matching(db_session, uuid.uuid4())


def test_task_chains_extraction_into_matching(db_engine, monkeypatch):
    import uuid

    from invoice_ops.workers import tasks

    calls: list[str] = []

    def fake_extract(session, invoice_id, **_kw):
        calls.append("extract")

        class _Stub:
            ok = True
            attempts = 1

        return _Stub()

    def fake_match(session, invoice_id):
        calls.append("match")
        return None

    monkeypatch.setattr("invoice_ops.services.extraction.run_extraction", fake_extract)
    monkeypatch.setattr("invoice_ops.services.matching.run_matching", fake_match)

    tasks.extract_invoice.run(str(uuid.uuid4()))

    assert calls == ["extract", "match"]
