from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from invoice_ops import db as db_module
from invoice_ops import models  # noqa: F401 - register tables on Base.metadata
from invoice_ops.api.main import create_app
from invoice_ops.config import get_settings
from invoice_ops.db import Base


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch) -> Iterator[None]:
    """Every test gets its own storage dir and eager task queue.

    Celery reads ``celery_task_always_eager`` once, at import time, into
    ``celery_app.conf`` (see queue.py) -- it never re-reads Settings. Setting
    the env var here only affects *future* get_settings() calls, so it cannot
    by itself change already-running Celery's behaviour. In CI the env var is
    "false" (a real broker) *before pytest starts*, so the first import bakes
    in eager=False permanently unless we reach into the live config directly.
    """
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    get_settings.cache_clear()

    from invoice_ops.queue import celery_app

    original_eager = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = original_eager
        get_settings.cache_clear()


@pytest.fixture
def db_engine() -> Iterator[object]:
    """A private in-memory SQLite DB, wired in as the module-level engine so
    request sessions *and* `session_scope()` (used by eager Celery tasks) share
    it."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)

    original = db_module.engine
    db_module.engine = engine
    db_module.SessionLocal.configure(bind=engine)
    try:
        yield engine
    finally:
        db_module.SessionLocal.configure(bind=original)
        db_module.engine = original
        engine.dispose()


@pytest.fixture
def db_session(db_engine) -> Iterator[Session]:
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def stub_enqueue(monkeypatch) -> MagicMock:
    """Stop `ingest_upload` from actually running extraction inline (eager mode).
    Tests that want the pipeline call `run_extraction` / the task directly."""
    stub = MagicMock(name="extract_invoice")
    monkeypatch.setattr("invoice_ops.services.ingestion.extract_invoice", stub)
    return stub


@pytest.fixture
def client(db_engine) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _make_pdf(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, text=text)
    return bytes(pdf.output())


@pytest.fixture
def make_pdf():
    return _make_pdf
