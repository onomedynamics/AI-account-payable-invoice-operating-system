from __future__ import annotations

import hashlib
import json

import pytest

from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Invoice, InvoiceSource
from invoice_ops.services.extraction import run_extraction
from invoice_ops.services.llm import LLMResponse
from invoice_ops.storage import get_storage

GOOD_RESULT = {
    "invoice": {
        "supplier_name": "ACME LTD",
        "invoice_number": "INV-42",
        "invoice_date": "2026-01-15",
        "currency": "EUR",
        "line_items": [
            {
                "description": "Widgets",
                "quantity": "10",
                "unit_price": "5.00",
                "line_total": "50.00",
            }
        ],
        "subtotal": "50.00",
        "tax_amount": "10.00",
        "total": "60.00",
    },
    "field_confidence": {"supplier_name": 0.95, "total": 0.9},
    "notes": None,
}


class FakeLLM:
    def __init__(self, *contents: str) -> None:
        self._contents = list(contents)
        self.calls: list[str] = []

    def complete_json(self, *, system: str, user: str, json_schema: dict) -> LLMResponse:
        self.calls.append(user)
        return LLMResponse(content=self._contents.pop(0), model="fake/model", raw={})


def _seed(
    session,
    data: bytes,
    *,
    content_type: str = "application/pdf",
    status: InvoiceStatus = InvoiceStatus.RECEIVED,
) -> Invoice:
    digest = hashlib.sha256(data).hexdigest()
    key = f"raw/{digest[:2]}/{digest}/doc"
    get_storage().put_object(key, data, content_type)
    invoice = Invoice(
        status=status,
        source=InvoiceSource.UPLOAD,
        original_filename="doc.pdf",
        content_type=content_type,
        size_bytes=len(data),
        storage_key=key,
        content_sha256=digest,
    )
    session.add(invoice)
    session.flush()
    return invoice


def test_happy_path_extracts_and_advances(db_session, make_pdf):
    invoice = _seed(db_session, make_pdf("ACME LTD\nINV-42\nTotal 60.00"))
    llm = FakeLLM(json.dumps(GOOD_RESULT))

    extraction = run_extraction(db_session, invoice.id, llm=llm)

    assert extraction is not None
    assert extraction.ok is True
    assert extraction.attempts == 1
    assert extraction.result_json["invoice"]["invoice_number"] == "INV-42"
    assert extraction.field_confidence["total"] == 0.9
    assert invoice.status == InvoiceStatus.EXTRACTED
    assert invoice.failure_reason is None
    assert len(llm.calls) == 1


def test_retries_once_then_succeeds(db_session, make_pdf):
    invoice = _seed(db_session, make_pdf("ACME LTD"))
    llm = FakeLLM("not json at all", json.dumps(GOOD_RESULT))

    extraction = run_extraction(db_session, invoice.id, llm=llm)

    assert extraction.ok is True
    assert extraction.attempts == 2
    assert invoice.status == InvoiceStatus.EXTRACTED
    # The retry prompt carried the parse error back to the model.
    assert "not valid JSON" in llm.calls[1]


def test_gives_up_after_retries_and_fails(db_session, make_pdf):
    invoice = _seed(db_session, make_pdf("ACME LTD"))
    llm = FakeLLM("garbage one", "garbage two")

    extraction = run_extraction(db_session, invoice.id, llm=llm)

    assert extraction.ok is False
    assert extraction.attempts == 2
    assert "JSON" in (extraction.error or "")
    assert extraction.raw_response == "garbage two"
    assert invoice.status == InvoiceStatus.FAILED
    assert invoice.failure_reason


def test_schema_violation_is_rejected(db_session, make_pdf):
    invoice = _seed(db_session, make_pdf("ACME LTD"))
    bad = json.dumps({"invoice": {"total": "10.00", "unknown_field": "x"}})
    llm = FakeLLM(bad, bad)

    extraction = run_extraction(db_session, invoice.id, llm=llm)

    assert extraction.ok is False
    assert invoice.status == InvoiceStatus.FAILED


def test_no_text_layer_fails_without_calling_llm(db_session):
    invoice = _seed(db_session, b"\x89PNG\r\n", content_type="image/png")
    llm = FakeLLM(json.dumps(GOOD_RESULT))

    extraction = run_extraction(db_session, invoice.id, llm=llm)

    assert extraction.ok is False
    assert "OCR" in (extraction.error or "")
    assert invoice.status == InvoiceStatus.FAILED
    assert llm.calls == []


def test_llm_unavailable_fails_cleanly(db_session, make_pdf, monkeypatch):
    invoice = _seed(db_session, make_pdf("ACME LTD"))

    def boom(*_args, **_kwargs):
        from invoice_ops.services.llm import LLMError

        raise LLMError("OPENROUTER_API_KEY is not set")

    # Force the real-client construction path to fail, deterministically and
    # without any network, regardless of what is in the environment.
    monkeypatch.setattr("invoice_ops.services.extraction.OpenRouterClient", boom)
    extraction = run_extraction(db_session, invoice.id)

    assert extraction is not None
    assert extraction.ok is False
    assert "OPENROUTER_API_KEY" in (extraction.error or "")
    assert invoice.status == InvoiceStatus.FAILED


def test_non_received_invoice_is_skipped(db_session, make_pdf):
    invoice = _seed(db_session, make_pdf("ACME LTD"), status=InvoiceStatus.EXTRACTED)
    llm = FakeLLM(json.dumps(GOOD_RESULT))

    assert run_extraction(db_session, invoice.id, llm=llm) is None
    assert invoice.status == InvoiceStatus.EXTRACTED
    assert llm.calls == []


def test_missing_invoice_raises(db_session):
    import uuid

    with pytest.raises(ValueError, match="not found"):
        run_extraction(db_session, uuid.uuid4())


def test_task_parses_id_and_delegates(db_engine, monkeypatch):
    import uuid

    from invoice_ops.workers import tasks

    seen: dict[str, object] = {}

    def fake_run(session, invoice_id, **_kw):
        seen["id"] = invoice_id
        return None

    monkeypatch.setattr("invoice_ops.services.extraction.run_extraction", fake_run)
    the_id = uuid.uuid4()
    tasks.extract_invoice.run(str(the_id))

    assert seen["id"] == the_id
