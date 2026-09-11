"""Shared invoice state-transition helper, used by every pipeline stage."""

from __future__ import annotations

from invoice_ops.domain.state import InvoiceStatus, can_transition
from invoice_ops.models import Invoice


def advance(invoice: Invoice, target: InvoiceStatus) -> None:
    if not can_transition(invoice.status, target):
        raise RuntimeError(f"illegal transition {invoice.status} -> {target}")
    invoice.status = target
