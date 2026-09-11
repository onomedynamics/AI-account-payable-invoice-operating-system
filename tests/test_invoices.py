from __future__ import annotations

import io

from fastapi.testclient import TestClient

from invoice_ops.config import get_settings

# Minimal valid-ish PDF header; enough for byte-level tests.
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _upload(client: TestClient, data: bytes, filename: str, content_type: str):
    return client.post(
        "/invoices",
        files={"file": (filename, io.BytesIO(data), content_type)},
    )


def test_upload_creates_invoice(client: TestClient, stub_enqueue):
    resp = _upload(client, PDF_BYTES, "acme-001.pdf", "application/pdf")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "received"
    assert body["source"] == "upload"
    assert body["size_bytes"] == len(PDF_BYTES)
    assert len(body["content_sha256"]) == 64
    assert body["storage_key"].startswith("raw/")
    assert body["original_filename"] == "acme-001.pdf"
    stub_enqueue.delay.assert_called_once_with(body["id"])


def test_upload_is_idempotent_on_identical_bytes(client: TestClient, stub_enqueue):
    first = _upload(client, PDF_BYTES, "acme-001.pdf", "application/pdf")
    assert first.status_code == 201

    # Same bytes, different filename -> same invoice, 200 not 201.
    second = _upload(client, PDF_BYTES, "renamed.pdf", "application/pdf")
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    # Extraction enqueued once, for the create only.
    stub_enqueue.delay.assert_called_once()


def test_distinct_bytes_create_distinct_invoices(client: TestClient):
    a = _upload(client, PDF_BYTES, "a.pdf", "application/pdf")
    b = _upload(client, PDF_BYTES + b"x", "b.pdf", "application/pdf")
    assert a.status_code == 201
    assert b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


def test_rejects_unsupported_content_type(client: TestClient):
    resp = _upload(client, b"hello", "notes.txt", "text/plain")
    assert resp.status_code == 415


def test_rejects_empty_file(client: TestClient):
    resp = _upload(client, b"", "empty.pdf", "application/pdf")
    assert resp.status_code == 400


def test_rejects_oversize_file(client: TestClient, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "16")
    get_settings.cache_clear()
    resp = _upload(client, PDF_BYTES, "big.pdf", "application/pdf")
    assert resp.status_code == 413


def test_stored_bytes_match_upload(client: TestClient):
    from invoice_ops.storage import get_storage

    resp = _upload(client, PDF_BYTES, "acme-002.pdf", "application/pdf")
    key = resp.json()["storage_key"]
    assert get_storage().get_object(key) == PDF_BYTES


def test_list_and_get_invoice(client: TestClient):
    assert client.get("/invoices").json() == []

    created = _upload(client, PDF_BYTES, "acme-003.pdf", "application/pdf").json()

    listing = client.get("/invoices").json()
    assert [i["id"] for i in listing] == [created["id"]]

    detail = client.get(f"/invoices/{created['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == created["id"]
    assert body["status"] == "received"
    assert body["matches"] == []
    assert body["extractions"] == []  # stub_enqueue kept extraction from running


def test_get_unknown_invoice_is_404(client: TestClient):
    resp = client.get("/invoices/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def _force_status(db_session, invoice_id: str, status) -> None:
    import uuid

    from invoice_ops.models import Invoice

    invoice = db_session.get(Invoice, uuid.UUID(invoice_id))
    invoice.status = status
    db_session.commit()


def test_approve_endpoint_records_audit_entry(client: TestClient, db_session):
    from invoice_ops.domain.state import InvoiceStatus

    created = _upload(client, PDF_BYTES, "a.pdf", "application/pdf").json()
    _force_status(db_session, created["id"], InvoiceStatus.NEEDS_REVIEW)

    resp = client.post(
        f"/invoices/{created['id']}/approve", json={"actor": "alice", "reason": "ok"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    detail = client.get(f"/invoices/{created['id']}").json()
    assert detail["audit_log"][-1]["actor"] == "alice"
    assert detail["audit_log"][-1]["to_status"] == "approved"
    assert detail["audit_log"][-1]["reason"] == "ok"


def test_reject_endpoint_uses_default_actor(client: TestClient, db_session):
    from invoice_ops.domain.state import InvoiceStatus

    created = _upload(client, PDF_BYTES, "b.pdf", "application/pdf").json()
    _force_status(db_session, created["id"], InvoiceStatus.NEEDS_REVIEW)

    resp = client.post(f"/invoices/{created['id']}/reject", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_approve_from_wrong_state_is_409(client: TestClient):
    created = _upload(client, PDF_BYTES, "c.pdf", "application/pdf").json()
    # still "received" -- never reached needs_review
    resp = client.post(f"/invoices/{created['id']}/approve", json={})
    assert resp.status_code == 409


def test_approve_unknown_invoice_is_404(client: TestClient):
    resp = client.post("/invoices/00000000-0000-0000-0000-000000000000/approve", json={})
    assert resp.status_code == 404


def test_approve_endpoint_enqueues_export(client: TestClient, db_session, stub_export_enqueue):
    from invoice_ops.domain.state import InvoiceStatus

    created = _upload(client, PDF_BYTES, "d.pdf", "application/pdf").json()
    _force_status(db_session, created["id"], InvoiceStatus.NEEDS_REVIEW)

    client.post(f"/invoices/{created['id']}/approve", json={})

    stub_export_enqueue.delay.assert_called_once_with(created["id"])


def test_reject_endpoint_does_not_enqueue_export(
    client: TestClient, db_session, stub_export_enqueue
):
    from invoice_ops.domain.state import InvoiceStatus

    created = _upload(client, PDF_BYTES, "e.pdf", "application/pdf").json()
    _force_status(db_session, created["id"], InvoiceStatus.NEEDS_REVIEW)

    client.post(f"/invoices/{created['id']}/reject", json={})

    stub_export_enqueue.delay.assert_not_called()
