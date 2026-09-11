from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pytest

from invoice_ops.domain.extraction import CRITICAL_FIELDS
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import (
    Extraction,
    Invoice,
    InvoiceMatch,
    InvoiceSource,
    PurchaseOrder,
    Vendor,
)
from invoice_ops.services.approval import record_human_decision, run_approval
from invoice_ops.services.extraction import run_extraction
from invoice_ops.services.llm import LLMResponse
from invoice_ops.services.matching import run_matching
from invoice_ops.services.validation import run_validation
from invoice_ops.storage import get_storage

GOOD_CONFIDENCE = {f: 0.95 for f in CRITICAL_FIELDS}
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
    "field_confidence": GOOD_CONFIDENCE,
    "notes": None,
}


class FakeLLM:
    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)

    def complete_json(self, *, system: str, user: str, json_schema: dict) -> LLMResponse:
        return LLMResponse(content=self._contents.pop(0), model="fake/model", raw={})


def _seed_vendor_and_po(session) -> tuple[Vendor, PurchaseOrder]:
    vendor = Vendor(legal_name="ACME Supplies Ltd", tax_id="IT01234567890", aliases=[])
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
    return vendor, po


def _seed_validated_invoice(
    session, vendor: Vendor, po: PurchaseOrder | None, *, suffix: str, has_po: bool = True
) -> Invoice:
    data = f"fixture-{suffix}".encode()
    digest = hashlib.sha256(data).hexdigest()
    key = f"raw/{digest[:2]}/{digest}/doc.pdf"
    get_storage().put_object(key, data, "application/pdf")

    invoice = Invoice(
        status=InvoiceStatus.VALIDATED,
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
            result_json=CLEAN_INVOICE,
            field_confidence=GOOD_CONFIDENCE,
        )
    )
    session.add(
        InvoiceMatch(
            invoice_id=invoice.id,
            vendor_id=vendor.id,
            vendor_match_method="tax_id",
            vendor_confidence=1.0,
            vendor_candidates=[],
            po_id=po.id if (po and has_po) else None,
            po_match_method="exact_number" if (po and has_po) else "not_found",
        )
    )
    session.flush()
    session.refresh(invoice)
    return invoice


def test_clean_invoice_auto_approves(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_validated_invoice(db_session, vendor, po, suffix="a")

    # run_approval reads the latest Validation row; seed one directly here
    # since this test targets the approval step in isolation.
    from invoice_ops.models import Validation

    db_session.add(Validation(invoice_id=invoice.id, results=[], passed=True))
    db_session.flush()
    db_session.refresh(invoice)

    decision = run_approval(db_session, invoice.id)

    assert decision is not None
    assert decision.outcome == InvoiceStatus.AUTO_APPROVED
    assert invoice.status == InvoiceStatus.AUTO_APPROVED
    last_entry = invoice.audit_log[-1]
    assert last_entry.from_status == "validated"
    assert last_entry.to_status == "auto_approved"


def test_no_po_forces_review(db_session):
    from invoice_ops.models import Validation

    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_validated_invoice(db_session, vendor, po, suffix="b", has_po=False)
    db_session.add(Validation(invoice_id=invoice.id, results=[], passed=True))
    db_session.flush()
    db_session.refresh(invoice)

    decision = run_approval(db_session, invoice.id)

    assert decision.outcome == InvoiceStatus.NEEDS_REVIEW
    assert invoice.status == InvoiceStatus.NEEDS_REVIEW


def test_non_validated_invoice_is_skipped(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_validated_invoice(db_session, vendor, po, suffix="c")
    invoice.status = InvoiceStatus.EXTRACTED
    db_session.flush()

    assert run_approval(db_session, invoice.id) is None


def test_missing_invoice_raises(db_session):
    import uuid

    with pytest.raises(ValueError, match="not found"):
        run_approval(db_session, uuid.uuid4())


def test_human_can_approve_from_needs_review(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_validated_invoice(db_session, vendor, po, suffix="d")
    invoice.status = InvoiceStatus.NEEDS_REVIEW
    db_session.flush()

    result = record_human_decision(
        db_session, invoice.id, approve=True, actor="alice@example.com", reason="looks fine"
    )

    assert result.status == InvoiceStatus.APPROVED
    entry = invoice.audit_log[-1]
    assert entry.actor == "alice@example.com"
    assert entry.reason == "looks fine"


def test_human_can_reject_from_needs_review(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_validated_invoice(db_session, vendor, po, suffix="e")
    invoice.status = InvoiceStatus.NEEDS_REVIEW
    db_session.flush()

    result = record_human_decision(db_session, invoice.id, approve=False, actor="bob")
    assert result.status == InvoiceStatus.REJECTED


def test_decision_from_wrong_state_raises(db_session):
    vendor, po = _seed_vendor_and_po(db_session)
    invoice = _seed_validated_invoice(db_session, vendor, po, suffix="f")
    # invoice is still VALIDATED, not NEEDS_REVIEW

    with pytest.raises(RuntimeError, match="illegal transition"):
        record_human_decision(db_session, invoice.id, approve=True, actor="carol")


def test_full_pipeline_audit_trail(db_session, make_pdf):
    """End to end: RECEIVED -> ... -> AUTO_APPROVED, every hop logged."""
    _seed_vendor_and_po(db_session)

    pdf = make_pdf("ACME Supplies Ltd\nINV-42\nPO-5567\nTotal 122.00")
    digest = hashlib.sha256(pdf).hexdigest()
    key = f"raw/{digest[:2]}/{digest}/doc.pdf"
    get_storage().put_object(key, pdf, "application/pdf")
    invoice = Invoice(
        status=InvoiceStatus.RECEIVED,
        source=InvoiceSource.UPLOAD,
        original_filename="doc.pdf",
        content_type="application/pdf",
        size_bytes=len(pdf),
        storage_key=key,
        content_sha256=digest,
    )
    db_session.add(invoice)
    db_session.flush()

    llm = FakeLLM(json.dumps(CLEAN_INVOICE))
    run_extraction(db_session, invoice.id, llm=llm)
    run_matching(db_session, invoice.id)
    run_validation(db_session, invoice.id)
    run_approval(db_session, invoice.id)

    assert invoice.status == InvoiceStatus.AUTO_APPROVED
    hops = [(e.from_status, e.to_status) for e in invoice.audit_log]
    assert hops == [
        ("received", "extracting"),
        ("extracting", "extracted"),
        ("extracted", "matching"),
        ("matching", "validating"),
        ("validating", "validated"),
        ("validated", "auto_approved"),
    ]
    assert all(e.reason for e in invoice.audit_log)


def test_task_chains_validation_into_approval(db_engine, monkeypatch):
    import uuid

    from invoice_ops.domain.approval import ApprovalDecision
    from invoice_ops.workers import tasks

    calls: list[str] = []

    def fake_validate(session, invoice_id):
        calls.append("validate")

        class _Stub:
            passed = True

        return _Stub()

    def fake_approve(session, invoice_id):
        calls.append("approve")
        return ApprovalDecision(outcome=InvoiceStatus.AUTO_APPROVED, reasons=())

    monkeypatch.setattr("invoice_ops.services.validation.run_validation", fake_validate)
    monkeypatch.setattr("invoice_ops.services.approval.run_approval", fake_approve)

    tasks.validate_invoice.run(str(uuid.uuid4()))

    assert calls == ["validate", "approve"]
