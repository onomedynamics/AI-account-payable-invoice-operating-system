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
