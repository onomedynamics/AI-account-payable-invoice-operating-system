"""Hand an approved invoice off to accounting.

AUTO_APPROVED or APPROVED -> EXPORTING -> EXPORTED, or -> FAILED only on a
genuine error (missing invoice, no successful extraction). The artifact is a
signed JSON document written to object storage; the schema it follows is
documented in docs/export-contract.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from invoice_ops.config import get_settings
from invoice_ops.domain.export import CONTRACT_VERSION, ExportPayload, sign_payload
from invoice_ops.domain.state import InvoiceStatus
from invoice_ops.models import AuditLog, Export, Invoice
from invoice_ops.services.extraction import latest_successful_extraction
from invoice_ops.services.lifecycle import advance
from invoice_ops.services.matching import latest_match
from invoice_ops.storage import get_storage

_EXPORTABLE_STATUSES = frozenset({InvoiceStatus.AUTO_APPROVED, InvoiceStatus.APPROVED})


def _approval_audit_entry(invoice: Invoice) -> AuditLog | None:
    for entry in reversed(invoice.audit_log):
        if entry.to_status in ("auto_approved", "approved"):
            return entry
    return None


def run_export(session: Session, invoice_id: uuid.UUID) -> Export | None:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise ValueError(f"invoice {invoice_id} not found")

    if invoice.status not in _EXPORTABLE_STATUSES:
        return None

    extraction = latest_successful_extraction(invoice)
    if extraction is None or extraction.result_json is None:
        invoice.failure_reason = "no successful extraction to export"
        advance(session, invoice, InvoiceStatus.FAILED, reason=invoice.failure_reason)
        session.flush()
        return None

    starting_status = invoice.status
    advance(session, invoice, InvoiceStatus.EXPORTING, reason="starting accounting export")
    session.flush()

    invoice_data = extraction.result_json.get("invoice", {})
    match = latest_match(invoice)
    approval_entry = _approval_audit_entry(invoice)

    payload = ExportPayload(
        contract_version=CONTRACT_VERSION,
        invoice_id=str(invoice.id),
        status=starting_status.value,
        supplier_name=invoice_data.get("supplier_name"),
        supplier_tax_id=invoice_data.get("supplier_tax_id"),
        invoice_number=invoice_data.get("invoice_number"),
        invoice_date=invoice_data.get("invoice_date"),
        currency=invoice_data.get("currency"),
        subtotal=invoice_data.get("subtotal"),
        tax_amount=invoice_data.get("tax_amount"),
        total=invoice_data.get("total"),
        line_items=invoice_data.get("line_items", []),
        vendor_id=str(match.vendor_id) if match and match.vendor_id else None,
        vendor_name=match.vendor.legal_name if match and match.vendor else None,
        po_id=str(match.po_id) if match and match.po_id else None,
        po_number=match.purchase_order.po_number if match and match.purchase_order else None,
        approved_by=approval_entry.actor if approval_entry else None,
        approved_reason=approval_entry.reason if approval_entry else None,
        exported_at=datetime.now(UTC).isoformat(),
    )
    payload_bytes = payload.to_json_bytes()
    settings = get_settings()
    signature = sign_payload(payload_bytes, settings.export_signing_secret)

    storage_key = f"exports/{invoice.id}/{uuid.uuid4()}.json"
    get_storage().put_object(storage_key, payload_bytes, "application/json")

    export = Export(
        invoice_id=invoice.id,
        storage_key=storage_key,
        contract_version=CONTRACT_VERSION,
        signature=signature,
    )
    session.add(export)
    advance(session, invoice, InvoiceStatus.EXPORTED, reason=f"exported to {storage_key}")
    session.flush()
    return export
