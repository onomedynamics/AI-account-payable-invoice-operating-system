"""Turn a validated invoice into an actual approve/review decision, and let a
human record the follow-up decision once it lands in review.

VALIDATED -> AUTO_APPROVED or NEEDS_REVIEW (run_approval); then, for anything
that needed a human, NEEDS_REVIEW -> APPROVED or REJECTED (record_human_decision).
The policy itself lives in domain/approval.py -- this module only gathers the
inputs it needs and writes the result.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from invoice_ops.domain.approval import ApprovalDecision, decide_approval
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Invoice, Validation
from invoice_ops.services.extraction import latest_successful_extraction
from invoice_ops.services.lifecycle import advance
from invoice_ops.services.matching import latest_match

# Vendor match methods that count as "confidently identified" for the policy.
# "ambiguous" and "none" do not.
_IDENTIFIED_METHODS = frozenset({"tax_id", "exact_name", "fuzzy_name"})


def latest_validation(invoice: Invoice) -> Validation | None:
    return invoice.validations[-1] if invoice.validations else None


def run_approval(session: Session, invoice_id: uuid.UUID) -> ApprovalDecision | None:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")

    if invoice.status != InvoiceStatus.VALIDATED:
        return None

    validation = latest_validation(invoice)
    extraction = latest_successful_extraction(invoice)
    match = latest_match(invoice)

    if validation is None or extraction is None:
        invoice.failure_reason = "missing validation or extraction data for approval"
        advance(session, invoice, InvoiceStatus.FAILED, reason=invoice.failure_reason)
        session.flush()
        return None

    vendor_identified = bool(
        match and match.vendor_id and match.vendor_match_method in _IDENTIFIED_METHODS
    )
    has_po = bool(match and match.po_id)

    decision = decide_approval(
        validation_passed=validation.passed,
        vendor_identified=vendor_identified,
        has_po=has_po,
        field_confidence=extraction.field_confidence or {},
    )
    reason = "; ".join(decision.reasons) if decision.reasons else "all checks passed"
    advance(session, invoice, decision.outcome, reason=reason)
    session.flush()
    return decision


def record_human_decision(
    session: Session,
    invoice_id: uuid.UUID,
    *,
    approve: bool,
    actor: str,
    reason: str | None = None,
) -> Invoice:
    """NEEDS_REVIEW -> APPROVED or REJECTED. Raises RuntimeError (via advance)
    if the invoice is not currently in NEEDS_REVIEW -- the API layer turns
    that into a 409."""
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")

    target = InvoiceStatus.APPROVED if approve else InvoiceStatus.REJECTED
    advance(session, invoice, target, actor=actor, reason=reason)
    session.flush()
    return invoice


def approve_and_export(
    session: Session, invoice_id: uuid.UUID, *, actor: str, reason: str | None = None
) -> Invoice:
    """What the two UI-facing callers (JSON API, review UI) use for the
    approve button: record the decision, then chain into export. Kept
    separate from record_human_decision (which stays a pure transition, no
    enqueue) so unit tests of the approval step alone are not also tests of
    the export step -- the same separation used for every other stage, where
    only the Celery task wrapper does chaining, never the service function."""
    invoice = record_human_decision(session, invoice_id, approve=True, actor=actor, reason=reason)

    # Commit before enqueuing: the export task opens its own session and must
    # be able to see this transition (same reasoning as ingest_upload).
    session.commit()
    from invoice_ops.workers.tasks import export_invoice

    export_invoice.delay(str(invoice.id))
    return invoice
