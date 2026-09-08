"""Celery tasks. M0 has only a liveness ping; the pipeline stages land in M2+."""

from __future__ import annotations

from invoice_ops.queue import celery_app


@celery_app.task(name="invoice_ops.ping")
def ping() -> str:
    return "pong"
