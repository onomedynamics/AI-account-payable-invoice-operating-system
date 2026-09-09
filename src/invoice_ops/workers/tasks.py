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

    if extraction is None:
        logger.info("extract_invoice: %s not in RECEIVED, skipped", invoice_id)
    else:
        logger.info(
            "extract_invoice: %s ok=%s attempts=%s",
            invoice_id,
            extraction.ok,
            extraction.attempts,
        )
