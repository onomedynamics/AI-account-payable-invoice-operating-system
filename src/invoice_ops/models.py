"""SQLAlchemy ORM models.

M1 added ``Invoice``; M2 adds ``Extraction``. Vendors, purchase orders,
validation results, approvals, and the audit log arrive in later milestones.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from invoice_ops.db import Base
from invoice_ops.domain.state import InvoiceStatus


class InvoiceSource(StrEnum):
    UPLOAD = "upload"
    EMAIL = "email"
    API = "api"


def _string_enum(enum_cls: type[Enum]) -> SAEnum:
    """A portable enum column.

    ``native_enum=False`` -> stored as VARCHAR, so the same migration runs on
    SQLite and Postgres. ``values_callable`` -> the DB stores the lowercase
    *values* ("received"), not the member names ("RECEIVED"), matching the API.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda cls: [str(member.value) for member in cls],
    )


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    status: Mapped[InvoiceStatus] = mapped_column(
        _string_enum(InvoiceStatus),
        default=InvoiceStatus.RECEIVED,
        nullable=False,
        index=True,
    )
    source: Mapped[InvoiceSource] = mapped_column(_string_enum(InvoiceSource), nullable=False)

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Exact-bytes idempotency key. The *business* duplicate check (same vendor +
    # invoice number + date + amount, possibly a different scan) is separate and
    # arrives in M4.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="Extraction.created_at",
    )

    def __repr__(self) -> str:
        return f"<Invoice {self.id} {self.status.value}>"


class Extraction(Base):
    """One extraction attempt for one invoice. Rows are never overwritten -- a
    re-run appends a new row, so the full history stays auditable."""

    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Provenance
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    text_method: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Outcome
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    field_confidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="extractions")

    def __repr__(self) -> str:
        return f"<Extraction {self.id} invoice={self.invoice_id} ok={self.ok}>"
