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
from invoice_ops.models import Invoice, InvoiceSource
from invoice_ops.services.ingestion import ingest_upload

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


class InvoiceDetailOut(InvoiceOut):
    extractions: list[ExtractionOut] = []


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
    if content_type not in settings.allowed_upload_content_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported content type: {content_type}",
        )

    data = file.file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"file exceeds {settings.max_upload_bytes} bytes",
        )

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
def get_invoice(invoice_id: uuid.UUID, db: Session = Depends(get_db)) -> Invoice:
    """One invoice with every extraction attempt made for it."""
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    return invoice
