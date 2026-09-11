"""Run one invoice through deterministic vendor + PO matching.

EXTRACTED -> MATCHING -> VALIDATING, or -> FAILED only on a genuine error (no
invoice, or no successful extraction to match against). An ambiguous or
missing vendor/PO match is a normal, recorded outcome -- never a failure, and
never silently resolved. What to *do* about it (auto-approve, route to a
human) is the approval policy's job -- see services.approval.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from invoice_ops.domain.extraction import ExtractedInvoice
from invoice_ops.domain.matching import PoIdentity, VendorIdentity, match_po, match_vendor
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Invoice, InvoiceMatch, PurchaseOrder, Vendor
from invoice_ops.services.extraction import latest_successful_extraction
from invoice_ops.services.lifecycle import advance


def run_matching(session: Session, invoice_id: uuid.UUID) -> InvoiceMatch | None:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")

    if invoice.status != InvoiceStatus.EXTRACTED:
        return None

    extraction = latest_successful_extraction(invoice)
    if extraction is None or extraction.result_json is None:
        invoice.failure_reason = "no successful extraction to match against"
        advance(session, invoice, InvoiceStatus.FAILED, reason=invoice.failure_reason)
        session.flush()
        return None

    advance(session, invoice, InvoiceStatus.MATCHING, reason="starting vendor + PO matching")
    session.flush()

    extracted = ExtractedInvoice.model_validate(extraction.result_json["invoice"])

    vendors = [
        VendorIdentity(
            vendor_id=v.id, legal_name=v.legal_name, tax_id=v.tax_id, aliases=tuple(v.aliases)
        )
        for v in session.scalars(select(Vendor).where(Vendor.active))
    ]
    vendor_result = match_vendor(extracted, vendors)

    # PO numbers are globally unique (the buyer issues them), so search all of
    # them rather than pre-filtering by the matched vendor. A PO found under a
    # *different* vendor than the extracted supplier is a real anomaly --
    # caught by domain.validation.rule_po_vendor_mismatch (M4), not dropped here.
    purchase_orders = [
        PoIdentity(po_id=po.id, po_number=po.po_number)
        for po in session.scalars(select(PurchaseOrder))
    ]
    po_result = match_po(extracted, purchase_orders)

    match = InvoiceMatch(
        invoice_id=invoice.id,
        vendor_id=vendor_result.vendor_id,
        vendor_match_method=vendor_result.method,
        vendor_confidence=vendor_result.confidence,
        vendor_candidates=[
            {"vendor_id": str(c.vendor_id), "legal_name": c.legal_name, "score": c.score}
            for c in vendor_result.candidates
        ],
        po_id=po_result.po_id,
        po_match_method=po_result.method,
    )
    session.add(match)
    advance(
        session,
        invoice,
        InvoiceStatus.VALIDATING,
        reason=f"vendor:{vendor_result.method} po:{po_result.method}",
    )
    session.flush()
    return match


def latest_match(invoice: Invoice) -> InvoiceMatch | None:
    """The most recent matching attempt, if any. Unlike Extraction, InvoiceMatch
    has no ok/not-ok split -- an unresolved vendor/PO is still a valid attempt,
    so "most recent" is simply the last one."""
    return invoice.matches[-1] if invoice.matches else None
