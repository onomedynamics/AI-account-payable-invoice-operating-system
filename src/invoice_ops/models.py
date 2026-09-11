"""SQLAlchemy ORM models.

M1 added ``Invoice``; M2 added ``Extraction``; M3 added ``Vendor``,
``PurchaseOrder``, ``PoLine``, ``InvoiceMatch``; M4 added ``Validation``;
M5 adds ``AuditLog``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from invoice_ops.db import Base
from invoice_ops.domain.state import InvoiceStatus


class InvoiceSource(StrEnum):
    UPLOAD = "upload"
    EMAIL = "email"
    API = "api"


def _utcnow() -> datetime:
    """Python-side (not server_default) timestamp, microsecond resolution on
    every backend. AuditLog needs this: several rows can be written for one
    invoice within the same wall-clock second, and SQLite's CURRENT_TIMESTAMP
    is only second-resolution -- ties would make `order_by=created_at`
    ambiguous exactly where strict chronological order matters most."""
    return datetime.now(UTC)


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
    # invoice number, possibly a different scan) is separate: see
    # domain.validation.rule_duplicate_invoice_number (M4).
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="Extraction.created_at",
    )
    matches: Mapped[list[InvoiceMatch]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceMatch.created_at",
    )
    validations: Mapped[list[Validation]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="Validation.created_at",
    )
    audit_log: Mapped[list[AuditLog]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="AuditLog.created_at",
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


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    legal_name: Mapped[str] = mapped_column(String(500), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    purchase_orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="vendor")

    def __repr__(self) -> str:
        return f"<Vendor {self.id} {self.legal_name!r}>"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    po_number: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Simple lifecycle for now: "open" | "closed" | "cancelled". A real enum
    # (with its own transition rules) can replace this if PO status logic grows.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")

    vendor: Mapped[Vendor] = relationship(back_populates="purchase_orders")
    lines: Mapped[list[PoLine]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<PurchaseOrder {self.id} {self.po_number!r}>"


class PoLine(Base):
    __tablename__ = "po_lines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class InvoiceMatch(Base):
    """One matching attempt for one invoice. Append-only, like Extraction --
    ambiguous or missing matches are recorded, never silently resolved."""

    __tablename__ = "invoice_matches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    vendor_match_method: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vendor_candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )

    po_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    po_match_method: Mapped[str] = mapped_column(String(32), nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="matches")
    vendor: Mapped[Vendor | None] = relationship()
    purchase_order: Mapped[PurchaseOrder | None] = relationship()

    def __repr__(self) -> str:
        return (
            f"<InvoiceMatch {self.id} invoice={self.invoice_id} "
            f"vendor={self.vendor_id} po={self.po_id}>"
        )


class Validation(Base):
    """One validation pass over one invoice: the full set of rule results at
    that moment. Append-only, like Extraction and InvoiceMatch."""

    __tablename__ = "validations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Each entry: {"rule": str, "severity": "info"|"warning"|"error",
    #              "passed": bool, "message": str, "detail": dict}
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # No rule at severity="error" failed. Does not by itself mean auto-approve
    # -- that gate also looks at extraction confidence (M5's job).
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    invoice: Mapped[Invoice] = relationship(back_populates="validations")

    def __repr__(self) -> str:
        return f"<Validation {self.id} invoice={self.invoice_id} passed={self.passed}>"


class AuditLog(Base):
    """One row per state transition, ever. Never updated, never deleted --
    written exclusively by services.lifecycle.advance(), which is the only
    place an invoice's status is allowed to change."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    # "system" for every automatic transition today. A human decision (the
    # /approve, /reject endpoints) records the reviewer here once there is
    # any notion of who that is -- no auth yet, so it defaults to "reviewer".
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="audit_log")

    def __repr__(self) -> str:
        return (
            f"<AuditLog {self.id} invoice={self.invoice_id} {self.from_status}->{self.to_status}>"
        )
