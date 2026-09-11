from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from invoice_ops.domain.approval import ApprovalDecision
from invoice_ops.domain.export import verify_signature
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import AuditLog, Extraction, Invoice, InvoiceMatch, InvoiceSource
from invoice_ops.services.export import run_export
from invoice_ops.storage import get_storage

RESULT_JSON = {
    "invoice": {
        "supplier_name": "ACME Supplies Ltd",
        "supplier_tax_id": "IT01234567890",
        "invoice_number": "INV-42",
        "invoice_date": "2026-01-15",
        "currency": "EUR",
        "purchase_order_number": "PO-5567",
        "line_items": [{"description": "Widget", "line_total": "100.00"}],
        "subtotal": "100.00",
        "tax_amount": "22.00",
        "total": "122.00",
    },
    "field_confidence": {"total": 0.95},
    "notes": None,
}


def _seed_exportable_invoice(session, *, status: InvoiceStatus, suffix: str) -> Invoice:
    data = f"fixture-export-{suffix}".encode()
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
            result_json=RESULT_JSON,
            field_confidence={"total": 0.95},
        )
    )
    session.add(
        InvoiceMatch(
            invoice_id=invoice.id,
            vendor_id=None,
            vendor_match_method="none",
            vendor_confidence=0.0,
            vendor_candidates=[],
            po_id=None,
            po_match_method="not_found",
        )
    )
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            from_status="validated",
            to_status=status.value,
            actor="alice@example.com",
            reason="all checks passed",
        )
    )
    session.flush()
    session.refresh(invoice)
    return invoice


def test_export_auto_approved_invoice_writes_signed_artifact(db_session):
    invoice = _seed_exportable_invoice(db_session, status=InvoiceStatus.AUTO_APPROVED, suffix="a")

    export = run_export(db_session, invoice.id)

    assert export is not None
    assert invoice.status == InvoiceStatus.EXPORTED
    stored = get_storage().get_object(export.storage_key)
    assert verify_signature(stored, "dev-insecure-signing-secret-change-me", export.signature)

    payload = json.loads(stored)
    assert payload["invoice_number"] == "INV-42"
    assert payload["supplier_name"] == "ACME Supplies Ltd"
    assert payload["approved_by"] == "alice@example.com"
    assert payload["approved_reason"] == "all checks passed"
    assert payload["contract_version"] == "1"


def test_export_approved_invoice_human_path(db_session):
    invoice = _seed_exportable_invoice(db_session, status=InvoiceStatus.APPROVED, suffix="b")
    export = run_export(db_session, invoice.id)
    assert export is not None
    assert invoice.status == InvoiceStatus.EXPORTED


def test_non_exportable_invoice_is_skipped(db_session):
    invoice = _seed_exportable_invoice(db_session, status=InvoiceStatus.VALIDATED, suffix="c")
    assert run_export(db_session, invoice.id) is None
    assert invoice.status == InvoiceStatus.VALIDATED


def test_missing_invoice_raises(db_session):
    with pytest.raises(ValueError, match="not found"):
        run_export(db_session, uuid.uuid4())


def test_task_chains_approval_into_export_on_auto_approve(
    db_engine, monkeypatch, stub_export_enqueue
):
    from invoice_ops.workers import tasks

    def fake_run_approval(session, invoice_id):
        return ApprovalDecision(outcome=InvoiceStatus.AUTO_APPROVED, reasons=())

    monkeypatch.setattr("invoice_ops.services.approval.run_approval", fake_run_approval)

    invoice_id = str(uuid.uuid4())
    tasks.approve_invoice.run(invoice_id)

    stub_export_enqueue.delay.assert_called_once_with(invoice_id)


def test_task_does_not_chain_into_export_on_needs_review(
    db_engine, monkeypatch, stub_export_enqueue
):
    from invoice_ops.workers import tasks

    def fake_run_approval(session, invoice_id):
        return ApprovalDecision(outcome=InvoiceStatus.NEEDS_REVIEW, reasons=("low confidence",))

    monkeypatch.setattr("invoice_ops.services.approval.run_approval", fake_run_approval)

    tasks.approve_invoice.run(str(uuid.uuid4()))

    stub_export_enqueue.delay.assert_not_called()
