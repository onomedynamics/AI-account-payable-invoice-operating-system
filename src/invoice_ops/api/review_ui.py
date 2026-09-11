"""Minimal server-rendered review UI.

Same app, same database session, same service layer as the JSON API in
invoices.py -- this module only adds HTML views and form-based endpoints on
top. No JavaScript, no build step: forms POST, the server redirects (PRG
pattern), the next GET renders the new state.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from invoice_ops.config import get_settings
from invoice_ops.db import get_db
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import Extraction, Invoice, InvoiceMatch, Validation
from invoice_ops.services.approval import record_human_decision
from invoice_ops.services.ingestion import UploadRejected, ingest_upload, validate_upload

router = APIRouter(prefix="/ui", tags=["review-ui"])
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_FieldRow = tuple[str, Any, float | None]


def _extraction_view(ex: Extraction) -> dict[str, Any]:
    invoice_data: dict[str, Any] = (ex.result_json or {}).get("invoice", {})
    confidence: dict[str, float] = ex.field_confidence or {}
    fields: list[_FieldRow] = [
        ("Supplier", invoice_data.get("supplier_name"), confidence.get("supplier_name")),
        ("Invoice #", invoice_data.get("invoice_number"), confidence.get("invoice_number")),
        ("Date", invoice_data.get("invoice_date"), confidence.get("invoice_date")),
        ("Currency", invoice_data.get("currency"), confidence.get("currency")),
        ("Total", invoice_data.get("total"), confidence.get("total")),
        ("PO number", invoice_data.get("purchase_order_number"), None),
    ]
    return {
        "created_at": ex.created_at,
        "model": ex.model,
        "attempts": ex.attempts,
        "ok": ex.ok,
        "error": ex.error,
        "fields": fields if ex.ok else [],
    }


def _match_view(m: InvoiceMatch) -> dict[str, Any]:
    return {
        "created_at": m.created_at,
        "vendor_name": m.vendor.legal_name if m.vendor else None,
        "vendor_method": m.vendor_match_method,
        "po_number": m.purchase_order.po_number if m.purchase_order else None,
        "po_method": m.po_match_method,
    }


def _validation_view(v: Validation) -> dict[str, Any]:
    return {"created_at": v.created_at, "passed": v.passed, "results": v.results}


@router.get("", response_class=RedirectResponse)
def ui_root() -> RedirectResponse:
    return RedirectResponse(url="/ui/invoices", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/invoices", response_class=HTMLResponse)
def ui_queue(
    request: Request,
    db: Session = Depends(get_db),
    status_filter: str | None = None,
) -> HTMLResponse:
    stmt = select(Invoice).order_by(Invoice.created_at.desc()).limit(200)
    if status_filter:
        stmt = stmt.where(Invoice.status == InvoiceStatus(status_filter))
    invoices = list(db.scalars(stmt))
    return _templates.TemplateResponse(
        request,
        "queue.html",
        {"invoices": invoices, "status_filter": status_filter},
    )


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def ui_invoice_detail(
    request: Request, invoice_id: uuid.UUID, db: Session = Depends(get_db)
) -> HTMLResponse:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")
    context = {
        "invoice": invoice,
        "extractions": [_extraction_view(e) for e in reversed(invoice.extractions)],
        "matches": [_match_view(m) for m in reversed(invoice.matches)],
        "validations": [_validation_view(v) for v in reversed(invoice.validations)],
        "audit_log": list(reversed(invoice.audit_log)),
        "can_decide": invoice.status == InvoiceStatus.NEEDS_REVIEW,
    }
    return _templates.TemplateResponse(request, "detail.html", context)


@router.post("/invoices", response_class=RedirectResponse)
def ui_upload(file: UploadFile, db: Session = Depends(get_db)) -> RedirectResponse:
    settings = get_settings()
    content_type = file.content_type or "application/octet-stream"
    data = file.file.read()
    try:
        validate_upload(data, content_type, settings)
    except UploadRejected as exc:
        url = f"/ui/invoices?flash={quote(exc.detail)}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    result = ingest_upload(
        db, data=data, filename=file.filename or "upload.bin", content_type=content_type
    )
    return RedirectResponse(
        url=f"/ui/invoices/{result.invoice.id}", status_code=status.HTTP_303_SEE_OTHER
    )


def _decide(
    db: Session, invoice_id: uuid.UUID, *, approve: bool, actor: str, reason: str
) -> RedirectResponse:
    try:
        record_human_decision(
            db,
            invoice_id,
            approve=approve,
            actor=actor.strip() or "reviewer",
            reason=reason.strip() or None,
        )
        flash = "Approved." if approve else "Rejected."
    except ValueError:
        return RedirectResponse(url="/ui/invoices", status_code=status.HTTP_303_SEE_OTHER)
    except RuntimeError as exc:
        flash = str(exc)
    url = f"/ui/invoices/{invoice_id}?flash={quote(flash)}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/invoices/{invoice_id}/approve", response_class=RedirectResponse)
def ui_approve(
    invoice_id: uuid.UUID,
    actor: str = Form("reviewer"),
    reason: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    return _decide(db, invoice_id, approve=True, actor=actor, reason=reason)


@router.post("/invoices/{invoice_id}/reject", response_class=RedirectResponse)
def ui_reject(
    invoice_id: uuid.UUID,
    actor: str = Form("reviewer"),
    reason: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    return _decide(db, invoice_id, approve=False, actor=actor, reason=reason)
