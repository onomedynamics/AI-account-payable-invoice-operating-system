"""SQLAlchemy ORM models.

M1 has only ``Invoice``. Vendors, purchase orders, extractions, validation
results, approvals, and the audit log arrive in later milestones.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum, StrEnum

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

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

    def __repr__(self) -> str:
        return f"<Invoice {self.id} {self.status.value}>"
