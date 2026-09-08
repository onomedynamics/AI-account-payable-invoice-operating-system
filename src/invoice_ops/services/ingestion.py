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

from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Invoice, InvoiceSource
from invoice_ops.storage import get_storage
from invoice_ops.workers.tasks import extract_invoice


@dataclass(frozen=True)
class IngestResult:
    invoice: Invoice
    created: bool


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

    # Kick off the (stubbed) extraction pipeline. Eager in dev, via Redis in prod.
    extract_invoice.delay(str(invoice.id))
    return IngestResult(invoice=invoice, created=True)
