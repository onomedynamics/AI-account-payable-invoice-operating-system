"""Invoice ingestion endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from invoice_ops.config import get_settings
from invoice_ops.db import get_db
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import InvoiceSource
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
    created_at: datetime
    updated_at: datetime


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
