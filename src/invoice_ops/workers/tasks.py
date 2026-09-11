"""Celery tasks."""

from __future__ import annotations

import uuid

from celery.utils.log import get_task_logger

from invoice_ops.queue import celery_app

logger = get_task_logger(__name__)


@celery_app.task(name="invoice_ops.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="invoice_ops.extract_invoice")
def extract_invoice(invoice_id: str) -> None:
    """Text-extract + LLM-extract one invoice. See services.extraction."""
    from invoice_ops.db import session_scope
    from invoice_ops.services.extraction import run_extraction

    with session_scope() as session:
        extraction = run_extraction(session, uuid.UUID(invoice_id))
        outcome = None if extraction is None else (extraction.ok, extraction.attempts)

    if outcome is None:
        logger.info("extract_invoice: %s not in RECEIVED, skipped", invoice_id)
    else:
        ok, attempts = outcome
        logger.info("extract_invoice: %s ok=%s attempts=%s", invoice_id, ok, attempts)
        # Chain into matching only when extraction actually succeeded -- a
        # failed invoice has no structured data to match against.
        if ok:
            match_invoice.delay(invoice_id)


@celery_app.task(name="invoice_ops.match_invoice")
def match_invoice(invoice_id: str) -> None:
    """Deterministic vendor + PO matching. See services.matching."""
    from invoice_ops.db import session_scope
    from invoice_ops.services.matching import run_matching

    with session_scope() as session:
        match = run_matching(session, uuid.UUID(invoice_id))
        summary = (
            None
            if match is None
            else (match.vendor_id, match.vendor_match_method, match.po_id, match.po_match_method)
        )

    if summary is None:
        logger.info("match_invoice: %s not in EXTRACTED, skipped", invoice_id)
    else:
        vendor_id, vendor_method, po_id, po_method = summary
        logger.info(
            "match_invoice: %s vendor=%s(%s) po=%s(%s)",
            invoice_id,
            vendor_id,
            vendor_method,
            po_id,
            po_method,
        )
        # Matching always produces a recorded outcome (even "no vendor found"),
        # so always chain into validation -- unlike extraction, there is no
        # "matching succeeded" gate here.
        validate_invoice.delay(invoice_id)


@celery_app.task(name="invoice_ops.validate_invoice")
def validate_invoice(invoice_id: str) -> None:
    """Run the deterministic rule set. See services.validation."""
    from invoice_ops.db import session_scope
    from invoice_ops.services.validation import run_validation

    with session_scope() as session:
        validation = run_validation(session, uuid.UUID(invoice_id))
        outcome = None if validation is None else validation.passed

    if outcome is None:
        logger.info("validate_invoice: %s not in VALIDATING, skipped", invoice_id)
    else:
        logger.info("validate_invoice: %s passed=%s", invoice_id, outcome)
        # Validation always produces a recorded outcome (pass or fail is still
        # a result), so always chain into the approval decision.
        approve_invoice.delay(invoice_id)


@celery_app.task(name="invoice_ops.approve_invoice")
def approve_invoice(invoice_id: str) -> None:
    """Decide auto-approve vs needs-review. See services.approval."""
    from invoice_ops.db import session_scope
    from invoice_ops.services.approval import run_approval

    with session_scope() as session:
        decision = run_approval(session, uuid.UUID(invoice_id))
        summary = None if decision is None else (decision.outcome.value, decision.reasons)

    if summary is None:
        logger.info("approve_invoice: %s not in VALIDATED, skipped", invoice_id)
    else:
        outcome, reasons = summary
        logger.info("approve_invoice: %s -> %s (%s)", invoice_id, outcome, "; ".join(reasons))
