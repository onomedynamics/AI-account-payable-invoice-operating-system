"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from invoice_ops import __version__
from invoice_ops.api import health, invoices
from invoice_ops.storage import get_storage


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Make sure the bucket / storage directory exists before serving.
    get_storage().ensure_ready()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="invoice-ops", version=__version__, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(invoices.router)
    return app


app = create_app()
