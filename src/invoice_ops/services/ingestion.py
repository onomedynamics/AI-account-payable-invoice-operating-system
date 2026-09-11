"""Ingest an uploaded document into an :class:`Invoice` row.

Idempotency is keyed on the SHA-256 of the raw bytes: the same file uploaded
twice yields the same invoice. The database ``UNIQUE(content_sha256)``
constraint is the source of truth -- the pre-check is just an optimisation, and
a concurrent insert that slips past it is caught as an ``IntegrityError`` and
resolved by re-reading the winning row.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from invoice_ops.config import Settings
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Invoice, InvoiceSource
from invoice_ops.storage import get_storage
from invoice_ops.workers.tasks import extract_invoice


@dataclass(frozen=True)
class IngestResult:
    invoice: Invoice
    created: bool


class UploadRejected(Exception):
    """A guard failed before any DB/storage write happened. Callers (the JSON
    API, the review UI) map status_code to their own response type."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def validate_upload(data: bytes, content_type: str, settings: Settings) -> None:
    """Shared guard for both the JSON API and the server-rendered UI, so the
    rules (allowed types, size limit, non-empty) live in exactly one place."""
    if content_type not in settings.allowed_upload_content_types:
        raise UploadRejected(415, f"unsupported content type: {content_type}")
    if not data:
        raise UploadRejected(400, "empty file")
    if len(data) > settings.max_upload_bytes:
        raise UploadRejected(413, f"file exceeds {settings.max_upload_bytes} bytes")


def _storage_key(digest: str, filename: str) -> str:
    # Strip any path components a client may have sent; keep a readable suffix.
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1] or "upload.bin"
    return f"raw/{digest[:2]}/{digest}/{basename}"


def ingest_upload(
    session: Session,
    *,
    data: bytes,
    filename: str,
    content_type: str,
) -> IngestResult:
    digest = hashlib.sha256(data).hexdigest()

    existing = session.scalar(select(Invoice).where(Invoice.content_sha256 == digest))
    if existing is not None:
        return IngestResult(invoice=existing, created=False)

    storage_key = _storage_key(digest, filename)
    get_storage().put_object(storage_key, data, content_type)

    invoice = Invoice(
        status=InvoiceStatus.RECEIVED,
        source=InvoiceSource.UPLOAD,
        original_filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        storage_key=storage_key,
        content_sha256=digest,
    )
    session.add(invoice)
    try:
        session.flush()
    except IntegrityError:
        # A concurrent request inserted the same bytes first. Roll back our
        # attempt and return the row that won.
        session.rollback()
        winner = session.scalar(select(Invoice).where(Invoice.content_sha256 == digest))
        if winner is None:
            raise
        return IngestResult(invoice=winner, created=False)

    # Commit before enqueuing: the extraction task opens its own session (its own
    # process, under a real broker) and must be able to see this row. A crash in
    # the gap between commit and enqueue leaves an invoice stuck in RECEIVED,
    # which a later sweeper can re-enqueue -- acceptable, and far better than
    # enqueuing work for a row that might roll back.
    session.commit()
    extract_invoice.delay(str(invoice.id))
    return IngestResult(invoice=invoice, created=True)
