"""Celery tasks.

M1 wires ``extract_invoice`` as a stub so the ingestion path is complete end to
end. M2 replaces the body with real OCR + schema-constrained LLM extraction.
"""

from __future__ import annotations

from celery.utils.log import get_task_logger

from invoice_ops.queue import celery_app

logger = get_task_logger(__name__)


@celery_app.task(name="invoice_ops.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="invoice_ops.extract_invoice")
def extract_invoice(invoice_id: str) -> None:
    """Stub until M2.

    Real work: load the raw document from storage, run OCR + layout analysis,
    call the LLM with a schema-constrained request, persist an ``Extraction``
    row with per-field confidence, and advance the invoice
    RECEIVED -> EXTRACTING -> EXTRACTED (or FAILED with a reason).
    """
    logger.info("extract_invoice stub invoked for invoice %s", invoice_id)
