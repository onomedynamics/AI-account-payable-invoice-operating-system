"""Build and sign the accounting-export artifact.

Pure functions: given already-fetched data, produce the canonical JSON payload
and its HMAC-SHA256 signature. No I/O -- storage and the DB row are the
service layer's job (services/export.py). This is the "mocked" half of M7:
there is no real accounting system on the other end, but the artifact is
genuinely signed and the schema is documented (docs/export-contract.md), so a
real integration could consume it unchanged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from typing import Any

CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class ExportPayload:
    contract_version: str
    invoice_id: str
    status: str
    supplier_name: str | None
    supplier_tax_id: str | None
    invoice_number: str | None
    invoice_date: str | None
    currency: str | None
    subtotal: str | None
    tax_amount: str | None
    total: str | None
    line_items: list[dict[str, Any]]
    vendor_id: str | None
    vendor_name: str | None
    po_id: str | None
    po_number: str | None
    approved_by: str | None
    approved_reason: str | None
    exported_at: str

    def to_json_bytes(self) -> bytes:
        # sort_keys + compact separators -> a canonical encoding, so the same
        # logical payload always signs to the same bytes.
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def verify_signature(payload_bytes: bytes, secret: str, signature: str) -> bool:
    expected = sign_payload(payload_bytes, secret)
    return hmac.compare_digest(expected, signature)
