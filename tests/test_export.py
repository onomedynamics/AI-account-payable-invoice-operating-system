from __future__ import annotations

from invoice_ops.domain.export import (
    CONTRACT_VERSION,
    ExportPayload,
    sign_payload,
    verify_signature,
)


def _payload(**overrides) -> ExportPayload:
    fields = {
        "contract_version": CONTRACT_VERSION,
        "invoice_id": "11111111-1111-1111-1111-111111111111",
        "status": "auto_approved",
        "supplier_name": "ACME Supplies Ltd",
        "supplier_tax_id": "IT01234567890",
        "invoice_number": "INV-42",
        "invoice_date": "2026-01-15",
        "currency": "EUR",
        "subtotal": "100.00",
        "tax_amount": "22.00",
        "total": "122.00",
        "line_items": [{"description": "Widget", "line_total": "100.00"}],
        "vendor_id": "22222222-2222-2222-2222-222222222222",
        "vendor_name": "ACME Supplies Ltd",
        "po_id": "33333333-3333-3333-3333-333333333333",
        "po_number": "PO-5567",
        "approved_by": "system",
        "approved_reason": "all checks passed",
        "exported_at": "2026-01-16T10:00:00+00:00",
    }
    fields.update(overrides)
    return ExportPayload(**fields)


def test_payload_serialization_is_deterministic():
    payload = _payload()
    assert payload.to_json_bytes() == payload.to_json_bytes()


def test_sign_and_verify_roundtrip():
    payload_bytes = _payload().to_json_bytes()
    signature = sign_payload(payload_bytes, "test-secret")
    assert verify_signature(payload_bytes, "test-secret", signature)


def test_verify_rejects_tampered_payload():
    original = _payload()
    signature = sign_payload(original.to_json_bytes(), "test-secret")

    tampered = _payload(total="999999.00").to_json_bytes()
    assert not verify_signature(tampered, "test-secret", signature)


def test_verify_rejects_wrong_secret():
    payload_bytes = _payload().to_json_bytes()
    signature = sign_payload(payload_bytes, "correct-secret")
    assert not verify_signature(payload_bytes, "wrong-secret", signature)


def test_different_payloads_sign_differently():
    a = sign_payload(_payload().to_json_bytes(), "s")
    b = sign_payload(_payload(total="1.00").to_json_bytes(), "s")
    assert a != b
