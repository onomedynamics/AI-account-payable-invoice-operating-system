"""Invoice ingestion endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from invoice_ops.config import get_settings
from invoice_ops.db import get_db
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Invoice, InvoiceMatch, InvoiceSource
from invoice_ops.services.approval import record_human_decision
from invoice_ops.services.ingestion import UploadRejected, ingest_upload, validate_upload

router = APIRouter(prefix="/invoices", tags=["invoices"])


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: InvoiceStatus
    source: InvoiceSource
    original_filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    content_sha256: str
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: uuid.UUID
    created_at: datetime
    model: str
    text_method: str
    attempts: int
    ok: bool
    result_json: dict[str, Any] | None
    field_confidence: dict[str, Any] | None
    error: str | None


class InvoiceMatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    vendor_id: uuid.UUID | None
    vendor_name: str | None = None
    vendor_match_method: str
    vendor_confidence: float
    vendor_candidates: list[dict[str, Any]]
    po_id: uuid.UUID | None
    po_number: str | None = None
    po_match_method: str

    @classmethod
    def from_model(cls, match: InvoiceMatch) -> InvoiceMatchOut:
        out = cls.model_validate(match)
        out.vendor_name = match.vendor.legal_name if match.vendor else None
        out.po_number = match.purchase_order.po_number if match.purchase_order else None
        return out


class ValidationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    passed: bool
    results: list[dict[str, Any]]


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    from_status: str
    to_status: str
    actor: str
    reason: str | None


class InvoiceDetailOut(InvoiceOut):
    extractions: list[ExtractionOut] = []
    matches: list[InvoiceMatchOut] = []
    validations: list[ValidationOut] = []
    audit_log: list[AuditLogOut] = []


class ReviewDecisionIn(BaseModel):
    actor: str = "reviewer"
    reason: str | None = None


@router.post("", response_model=InvoiceOut)
def upload_invoice(
    file: UploadFile,
    response: Response,
    db: Session = Depends(get_db),
) -> InvoiceOut:
    """Accept a single invoice document.

    201 on a new invoice, 200 when the exact bytes were already ingested.
    Sync handler on purpose: FastAPI runs it in a worker thread, so the
    blocking storage + DB calls do not stall the event loop.
    """
    settings = get_settings()
    content_type = file.content_type or "application/octet-stream"
    data = file.file.read()
    try:
        validate_upload(data, content_type, settings)
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    result = ingest_upload(
        db,
        data=data,
        filename=file.filename or "upload.bin",
        content_type=content_type,
    )
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return InvoiceOut.model_validate(result.invoice)


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Invoice]:
    """Invoices, newest first."""
    stmt = select(Invoice).order_by(Invoice.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


@router.get("/{invoice_id}", response_model=InvoiceDetailOut)
def get_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_db)) -> InvoiceDetailOut:
    """One invoice with every extraction attempt and every matching attempt."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    detail = InvoiceDetailOut.model_validate(invoice)
    # Resolve vendor_name / po_number from the relationships -- model_validate's
    # automatic attribute lookup can't reach through a join by itself.
    detail.matches = [InvoiceMatchOut.from_model(m) for m in invoice.matches]
    return detail


@router.post("/{invoice_id}/approve", response_model=InvoiceOut)
def approve_invoice_endpoint(
    invoice_id: uuid.UUID,
    body: ReviewDecisionIn,
    db: Session = Depends(get_db),
) -> InvoiceOut:
    """Record a human approval. Only legal from NEEDS_REVIEW."""
    return _record_decision(invoice_id, body, db, approve=True)


@router.post("/{invoice_id}/reject", response_model=InvoiceOut)
def reject_invoice_endpoint(
    invoice_id: uuid.UUID,
    body: ReviewDecisionIn,
    db: Session = Depends(get_db),
) -> InvoiceOut:
    """Record a human rejection. Only legal from NEEDS_REVIEW."""
    return _record_decision(invoice_id, body, db, approve=False)


def _record_decision(
    invoice_id: uuid.UUID, body: ReviewDecisionIn, db: Session, *, approve: bool
) -> InvoiceOut:
    try:
        invoice = record_human_decision(
            db, invoice_id, approve=approve, actor=body.actor, reason=body.reason
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return InvoiceOut.model_validate(invoice)
