"""Shared invoice state-transition helper, used by every pipeline stage.

This is the *only* place an invoice's status is allowed to change: every call
writes an AuditLog row first, so the full transition history is reconstructible
for any invoice without touching application code.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from invoice_ops.domain.state import InvoiceStatus, can_transition
from invoice_ops.models import AuditLog, Invoice


def advance(
    session: Session,
    invoice: Invoice,
    target: InvoiceStatus,
    *,
    actor: str = "system",
    reason: str | None = None,
) -> None:
    if not can_transition(invoice.status, target):
        raise RuntimeError(f"illegal transition {invoice.status} -> {target}")
    session.add(
        AuditLog(
            invoice_id=invoice.id,
            from_status=invoice.status.value,
            to_status=target.value,
            actor=actor,
            reason=reason,
        )
    )
    invoice.status = target
