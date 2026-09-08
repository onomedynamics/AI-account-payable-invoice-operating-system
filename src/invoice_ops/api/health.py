"""Liveness and readiness probes.

``/health/live``  -- is the process up? (no dependency checks)
``/health/ready`` -- can it serve traffic? (DB, storage, broker reachable)

The split matters: an orchestrator restarts on failed liveness but only stops
routing traffic on failed readiness.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from invoice_ops.config import get_settings
from invoice_ops.db import check_connection
from invoice_ops.storage import get_storage

router = APIRouter(tags=["health"])


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"]
    dependencies: list[DependencyStatus]


def _check(name: str, probe: Callable[[], None]) -> DependencyStatus:
    try:
        probe()
    except Exception as exc:
        # Report any failure as a degraded dependency; never crash the probe.
        return DependencyStatus(name=name, ok=False, detail=str(exc))
    return DependencyStatus(name=name, ok=True)


def _check_broker() -> None:
    settings = get_settings()
    if settings.celery_task_always_eager:
        return
    from invoice_ops.queue import celery_app

    with celery_app.connection() as conn:
        conn.ensure_connection(max_retries=1, timeout=2)


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=ReadyResponse)
def ready(response: Response) -> ReadyResponse:
    deps = [
        _check("database", check_connection),
        _check("storage", lambda: get_storage().ensure_ready()),
        _check("broker", _check_broker),
    ]
    all_ok = all(d.ok for d in deps)
    if not all_ok:
        response.status_code = 503
    return ReadyResponse(status="ok" if all_ok else "degraded", dependencies=deps)
