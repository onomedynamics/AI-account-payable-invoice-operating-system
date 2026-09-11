"""Run one invoice through text extraction + LLM structured extraction.

The state machine here is deliberately narrow: an attempt lands the invoice in
EXTRACTED (with a confidence map riding along in the data) or FAILED. It never
routes to NEEDS_REVIEW -- that decision belongs to the validation stage (M4/M5),
which is the single place low confidence is acted on.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy.orm import Session

from invoice_ops.config import get_settings
from invoice_ops.domain.extraction import ExtractionResult, result_json_schema
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Extraction, Invoice
from invoice_ops.services.documents import extract_text
from invoice_ops.services.lifecycle import advance
from invoice_ops.services.llm import LLMError, OpenRouterClient, StructuredLLM
from invoice_ops.storage import get_storage

_MAX_TEXT_CHARS = 20_000

_SYSTEM_PROMPT = (
    "You extract structured data from a single supplier invoice. "
    "Return ONLY a JSON object matching the schema. "
    "Use null for any field not clearly present in the text -- never guess. "
    "Amounts are plain numbers: no currency symbols, no thousands separators. "
    "Dates are ISO 8601 (YYYY-MM-DD). "
    "In field_confidence give a 0..1 score for every field you populated in `invoice`."
)


class ExtractionFailed(RuntimeError):
    def __init__(self, message: str, attempts: int, raw_response: str | None) -> None:
        super().__init__(message)
        self.attempts = attempts
        self.raw_response = raw_response


@dataclass(frozen=True)
class RetryOutcome:
    result: ExtractionResult
    attempts: int
    raw_response: str
    model: str


def _user_prompt(document_text: str, *, error_hint: str | None) -> str:
    parts = [
        "JSON Schema:",
        json.dumps(result_json_schema()),
        "",
        "Invoice text:",
        '"""',
        document_text[:_MAX_TEXT_CHARS],
        '"""',
    ]
    if error_hint:
        parts += [
            "",
            "Your previous response was rejected. Fix this and return valid JSON:",
            error_hint,
        ]
    return "\n".join(parts)


def extract_with_retry(llm: StructuredLLM, document_text: str, *, max_retries: int) -> RetryOutcome:
    schema = result_json_schema()
    error_hint: str | None = None
    last_raw = ""
    last_model = ""
    errors: list[str] = []
    attempt = 0

    for attempt in range(1, max_retries + 2):
        response = llm.complete_json(
            system=_SYSTEM_PROMPT,
            user=_user_prompt(document_text, error_hint=error_hint),
            json_schema=schema,
        )
        last_raw = response.content
        last_model = response.model

        try:
            data = json.loads(response.content)
        except json.JSONDecodeError as exc:
            error_hint = f"response was not valid JSON: {exc}"
            errors.append(error_hint)
            continue

        try:
            result = ExtractionResult.model_validate(data)
        except ValidationError as exc:
            error_hint = f"JSON did not match the schema: {exc}"
            errors.append(error_hint)
            continue

        return RetryOutcome(
            result=result, attempts=attempt, raw_response=last_raw, model=last_model
        )

    raise ExtractionFailed("; ".join(errors), attempt, last_raw)


def run_extraction(
    session: Session,
    invoice_id: uuid.UUID,
    *,
    llm: StructuredLLM | None = None,
) -> Extraction | None:
    settings = get_settings()
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")

    if invoice.status != InvoiceStatus.RECEIVED:
        # Already processed (or being processed). Do not create a second attempt.
        return None

    advance(invoice, InvoiceStatus.EXTRACTING)
    session.flush()

    document = extract_text(get_storage().get_object(invoice.storage_key), invoice.content_type)

    if document.is_empty:
        return _fail(
            session,
            invoice,
            model=settings.llm_model,
            text_method=document.method,
            error="no extractable text layer (OCR fallback not implemented yet)",
            attempts=0,
            raw_response=None,
        )

    try:
        client: StructuredLLM = llm or OpenRouterClient(settings)
    except LLMError as exc:
        return _fail(
            session,
            invoice,
            model=settings.llm_model,
            text_method=document.method,
            error=str(exc),
            attempts=0,
            raw_response=None,
        )

    try:
        outcome = extract_with_retry(client, document.text, max_retries=settings.llm_max_retries)
    except ExtractionFailed as exc:
        return _fail(
            session,
            invoice,
            model=settings.llm_model,
            text_method=document.method,
            error=str(exc),
            attempts=exc.attempts,
            raw_response=exc.raw_response,
        )

    extraction = Extraction(
        invoice_id=invoice.id,
        model=outcome.model or settings.llm_model,
        text_method=document.method,
        attempts=outcome.attempts,
        ok=True,
        result_json=outcome.result.model_dump(mode="json"),
        field_confidence=dict(outcome.result.field_confidence),
        raw_response=outcome.raw_response,
    )
    session.add(extraction)
    invoice.failure_reason = None
    advance(invoice, InvoiceStatus.EXTRACTED)
    session.flush()
    return extraction


def latest_successful_extraction(invoice: Invoice) -> Extraction | None:
    """The most recent ok=True attempt, if any. Retries mean several attempts
    can exist for one invoice; only the successful one matters downstream."""
    for extraction in reversed(invoice.extractions):
        if extraction.ok:
            return extraction
    return None


def _fail(
    session: Session,
    invoice: Invoice,
    *,
    model: str,
    text_method: str,
    error: str,
    attempts: int,
    raw_response: str | None,
) -> Extraction:
    extraction = Extraction(
        invoice_id=invoice.id,
        model=model,
        text_method=text_method,
        attempts=attempts,
        ok=False,
        error=error,
        raw_response=raw_response,
    )
    session.add(extraction)
    invoice.failure_reason = error
    advance(invoice, InvoiceStatus.FAILED)
    session.flush()
    return extraction
