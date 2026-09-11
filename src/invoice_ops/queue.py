"""Celery application.

In dev, ``celery_task_always_eager`` makes ``.delay()`` run the task inline in
the caller, so no broker process is required. In CI / prod the same tasks run
through Redis.

Settings are read once here, at import time, into ``celery_app.conf`` -- this
module does not re-read them later. That is fine for a real process (env vars
don't change after startup) but means tests cannot flip eager mode via
``monkeypatch.setenv`` + ``get_settings.cache_clear()`` alone; they must
reach into ``celery_app.conf.task_always_eager`` directly (see
``tests/conftest.py::_isolated_env``).
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
