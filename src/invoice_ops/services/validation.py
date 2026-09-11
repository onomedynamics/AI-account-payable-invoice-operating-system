"""Run the deterministic rule set over one invoice.

VALIDATING -> VALIDATED, or -> FAILED only on a genuine error (no invoice, or
no successful extraction). This stage never decides auto-approve vs
needs-review -- see domain/validation.py's module docstring and M5.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from invoice_ops.domain.extraction import ExtractedInvoice
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.domain.validation import (
    DuplicateCandidate,
    PoSnapshot,
    ValidationInput,
    has_blocking_failure,
    run_rules,
)
from invoice_ops.models import Invoice, InvoiceMatch, PurchaseOrder, Validation
from invoice_ops.services.extraction import latest_successful_extraction
from invoice_ops.services.lifecycle import advance
from invoice_ops.services.matching import latest_match

# Statuses that mean "not a live invoice competing for the same number" --
# excluded from the duplicate check so a rejected/failed retry doesn't
# permanently block a resubmission under the same invoice number.
_EXCLUDED_FROM_DUPLICATE_CHECK = frozenset({InvoiceStatus.FAILED, InvoiceStatus.REJECTED})


def _find_duplicate_candidates(
    session: Session, invoice: Invoice, vendor_id: uuid.UUID, invoice_number: str
) -> list[DuplicateCandidate]:
    """Other invoices for the same vendor whose extracted invoice_number matches.

    invoice_number lives inside Extraction.result_json (JSON), not a queryable
    column, and JSON querying isn't portable across SQLite/Postgres -- so this
    narrows by the cheap, indexed vendor_id column first, then compares numbers
    in Python. Fine at this project's scale; a production system would want a
    normalized (vendor_id, invoice_number) index instead.
    """
    normalized = invoice_number.strip().lower()
    other_ids = session.scalars(
        select(InvoiceMatch.invoice_id)
        .where(InvoiceMatch.vendor_id == vendor_id, InvoiceMatch.invoice_id != invoice.id)
        .distinct()
    ).all()

    candidates: list[DuplicateCandidate] = []
    for other_id in other_ids:
        other = session.get(Invoice, other_id)
        if other is None or other.status in _EXCLUDED_FROM_DUPLICATE_CHECK:
            continue
        other_extraction = latest_successful_extraction(other)
        if other_extraction is None or other_extraction.result_json is None:
            continue
        other_number = other_extraction.result_json.get("invoice", {}).get("invoice_number")
        if other_number and other_number.strip().lower() == normalized:
            candidates.append(
                DuplicateCandidate(
                    invoice_id=other.id, invoice_number=other_number, status=other.status.value
                )
            )
    return candidates


def run_validation(session: Session, invoice_id: uuid.UUID) -> Validation | None:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")

    if invoice.status != InvoiceStatus.VALIDATING:
        return None

    extraction = latest_successful_extraction(invoice)
    if extraction is None or extraction.result_json is None:
        invoice.failure_reason = "no successful extraction to validate"
        advance(invoice, InvoiceStatus.FAILED)
        session.flush()
        return None

    extracted = ExtractedInvoice.model_validate(extraction.result_json["invoice"])

    match = latest_match(invoice)
    matched_po = None
    if match is not None and match.po_id is not None:
        po = session.get(PurchaseOrder, match.po_id)
        if po is not None:
            matched_po = PoSnapshot(
                po_number=po.po_number, currency=po.currency, total=po.total, vendor_id=po.vendor_id
            )

    duplicate_candidates: list[DuplicateCandidate] = []
    if match is not None and match.vendor_id is not None and extracted.invoice_number:
        duplicate_candidates = _find_duplicate_candidates(
            session, invoice, match.vendor_id, extracted.invoice_number
        )

    validation_input = ValidationInput(
        invoice=extracted,
        matched_po=matched_po,
        matched_vendor_id=match.vendor_id if match else None,
        duplicate_candidates=tuple(duplicate_candidates),
    )
    results = run_rules(validation_input)

    validation = Validation(
        invoice_id=invoice.id,
        results=[asdict(r) for r in results],
        passed=not has_blocking_failure(results),
    )
    session.add(validation)
    advance(invoice, InvoiceStatus.VALIDATED)
    session.flush()
    return validation
