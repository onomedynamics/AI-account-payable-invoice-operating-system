from __future__ import annotations

import hashlib
import io
import uuid
from decimal import Decimal

from fastapi.testclient import TestClient

from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import (
    AuditLog,
    Extraction,
    Invoice,
    InvoiceMatch,
    InvoiceSource,
    PurchaseOrder,
    Validation,
    Vendor,
)
from invoice_ops.storage import get_storage

PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _upload(client: TestClient, data: bytes, filename: str, content_type: str, **kw):
    return client.post(
        "/ui/invoices",
        files={"file": (filename, io.BytesIO(data), content_type)},
        **kw,
    )


def _force_status(db_session, invoice_id: uuid.UUID, target: InvoiceStatus) -> None:
    invoice = db_session.get(Invoice, invoice_id)
    invoice.status = target
    db_session.commit()


def test_queue_page_empty(client: TestClient):
    resp = client.get("/ui/invoices")
    assert resp.status_code == 200
    assert "No invoices yet" in resp.text


def test_ui_root_redirects_to_queue(client: TestClient):
    resp = client.get("/ui", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/ui/invoices"


def test_upload_via_ui_lands_on_detail_page(client: TestClient, stub_enqueue):
    resp = _upload(client, PDF_BYTES, "acme.pdf", "application/pdf", follow_redirects=True)
    assert resp.status_code == 200
    assert "acme.pdf" in resp.text
    assert "received" in resp.text


def test_upload_via_ui_rejects_bad_type(client: TestClient):
    resp = client.post(
        "/ui/invoices",
        files={"file": ("notes.txt", io.BytesIO(b"hi"), "text/plain")},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "unsupported content type" in resp.text


def test_queue_lists_uploaded_invoice(client: TestClient, stub_enqueue):
    _upload(client, PDF_BYTES, "acme.pdf", "application/pdf")
    resp = client.get("/ui/invoices")
    assert "acme.pdf" in resp.text


def test_needs_review_filter(client: TestClient, db_session, stub_enqueue):
    resp = _upload(client, PDF_BYTES, "acme.pdf", "application/pdf", follow_redirects=True)
    invoice_id = resp.url.path.rsplit("/", 1)[-1]
    _force_status(db_session, uuid.UUID(invoice_id), InvoiceStatus.NEEDS_REVIEW)

    all_page = client.get("/ui/invoices")
    filtered = client.get("/ui/invoices?status_filter=needs_review")
    assert "acme.pdf" in all_page.text
    assert "acme.pdf" in filtered.text


def test_decision_box_hidden_until_needs_review(client: TestClient, db_session, stub_enqueue):
    resp = _upload(client, PDF_BYTES, "acme.pdf", "application/pdf", follow_redirects=True)
    invoice_id = resp.url.path.rsplit("/", 1)[-1]

    assert "Review decision" not in resp.text

    _force_status(db_session, uuid.UUID(invoice_id), InvoiceStatus.NEEDS_REVIEW)
    resp2 = client.get(f"/ui/invoices/{invoice_id}")
    assert "Review decision" in resp2.text


def test_approve_via_ui_updates_status_and_audit(client: TestClient, db_session, stub_enqueue):
    resp = _upload(client, PDF_BYTES, "acme.pdf", "application/pdf", follow_redirects=True)
    invoice_id = resp.url.path.rsplit("/", 1)[-1]
    _force_status(db_session, uuid.UUID(invoice_id), InvoiceStatus.NEEDS_REVIEW)

    approve_resp = client.post(
        f"/ui/invoices/{invoice_id}/approve",
        data={"actor": "alice", "reason": "looks good"},
        follow_redirects=True,
    )
    assert approve_resp.status_code == 200
    assert "badge-approved" in approve_resp.text
    assert "alice" in approve_resp.text
    assert "looks good" in approve_resp.text


def test_reject_via_ui(client: TestClient, db_session, stub_enqueue):
    resp = _upload(client, PDF_BYTES, "acme.pdf", "application/pdf", follow_redirects=True)
    invoice_id = resp.url.path.rsplit("/", 1)[-1]
    _force_status(db_session, uuid.UUID(invoice_id), InvoiceStatus.NEEDS_REVIEW)

    reject_resp = client.post(f"/ui/invoices/{invoice_id}/reject", data={}, follow_redirects=True)
    assert "badge-rejected" in reject_resp.text


def test_decide_from_wrong_state_flashes_and_does_not_change_status(
    client: TestClient, stub_enqueue
):
    resp = _upload(client, PDF_BYTES, "acme.pdf", "application/pdf", follow_redirects=True)
    invoice_id = resp.url.path.rsplit("/", 1)[-1]
    # still "received", never reached needs_review

    approve_resp = client.post(f"/ui/invoices/{invoice_id}/approve", data={}, follow_redirects=True)
    assert approve_resp.status_code == 200
    assert "illegal transition" in approve_resp.text
    assert "badge-received" in approve_resp.text


def test_detail_page_shows_full_pipeline_output(client: TestClient, db_session):
    vendor = Vendor(legal_name="ACME Supplies Ltd", tax_id="IT01234567890", aliases=[])
    db_session.add(vendor)
    db_session.flush()
    po = PurchaseOrder(
        po_number="PO-5567",
        vendor_id=vendor.id,
        currency="EUR",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("22.00"),
        total=Decimal("122.00"),
        status="open",
    )
    db_session.add(po)
    db_session.flush()

    data = b"fixture-ui-detail"
    digest = hashlib.sha256(data).hexdigest()
    key = f"raw/{digest[:2]}/{digest}/doc.pdf"
    get_storage().put_object(key, data, "application/pdf")
    invoice = Invoice(
        status=InvoiceStatus.AUTO_APPROVED,
        source=InvoiceSource.UPLOAD,
        original_filename="doc.pdf",
        content_type="application/pdf",
        size_bytes=len(data),
        storage_key=key,
        content_sha256=digest,
    )
    db_session.add(invoice)
    db_session.flush()

    result_json = {
        "invoice": {
            "supplier_name": "ACME Supplies Ltd",
            "invoice_number": "INV-42",
            "invoice_date": "2026-01-15",
            "currency": "EUR",
            "purchase_order_number": "PO-5567",
            "line_items": [],
            "total": "122.00",
        },
        "field_confidence": {"total": 0.95},
        "notes": None,
    }
    db_session.add(
        Extraction(
            invoice_id=invoice.id,
            model="fake/model",
            text_method="pdfplumber",
            attempts=1,
            ok=True,
            result_json=result_json,
            field_confidence={"total": 0.95},
        )
    )
    db_session.add(
        InvoiceMatch(
            invoice_id=invoice.id,
            vendor_id=vendor.id,
            vendor_match_method="tax_id",
            vendor_confidence=1.0,
            vendor_candidates=[],
            po_id=po.id,
            po_match_method="exact_number",
        )
    )
    db_session.add(
        Validation(
            invoice_id=invoice.id,
            results=[
                {
                    "rule": "po_amount_tolerance",
                    "severity": "info",
                    "passed": True,
                    "message": "invoice total within tolerance of PO PO-5567",
                    "detail": {},
                }
            ],
            passed=True,
        )
    )
    db_session.add(
        AuditLog(
            invoice_id=invoice.id,
            from_status="validated",
            to_status="auto_approved",
            actor="system",
            reason="all checks passed",
        )
    )
    db_session.commit()

    resp = client.get(f"/ui/invoices/{invoice.id}")
    assert resp.status_code == 200
    assert "ACME Supplies Ltd" in resp.text
    assert "INV-42" in resp.text
    assert "PO-5567" in resp.text
    assert "po_amount_tolerance" in resp.text
    assert "all checks passed" in resp.text


def test_unknown_invoice_detail_is_404(client: TestClient):
    resp = client.get("/ui/invoices/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
