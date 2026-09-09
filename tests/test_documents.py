from __future__ import annotations

import pytest

from invoice_ops.services.documents import extract_text


def test_born_digital_pdf_yields_text(make_pdf):
    data = make_pdf("ACME LTD\nInvoice INV-42\nTotal 123.45")
    doc = extract_text(data, "application/pdf")

    assert doc.method == "pdfplumber"
    assert doc.page_count == 1
    assert not doc.is_empty
    assert "ACME LTD" in doc.text
    assert "INV-42" in doc.text


def test_image_upload_returns_empty_pending_ocr():
    doc = extract_text(b"\x89PNG\r\n\x1a\n", "image/png")
    assert doc.is_empty
    assert doc.method == "ocr"


def test_unsupported_type_raises():
    with pytest.raises(ValueError, match="content type"):
        extract_text(b"hello", "text/plain")
