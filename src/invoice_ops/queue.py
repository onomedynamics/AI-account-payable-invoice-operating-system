"""Celery application.

In dev, ``celery_task_always_eager`` makes ``.delay()`` run the task inline in
the caller, so no broker process is required. In CI / prod the same tasks run
through Redis.
"""

from __future__ import annotations

from celery import Celery

from invoice_ops.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "invoice_ops",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
)
celery_app.conf.update(
    task_always_eager=_settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
)
celery_app.autodiscover_tasks(["invoice_ops.workers"])
