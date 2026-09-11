# Accounting export contract

What a downstream accounting system receives when an invoice is exported.
This is the **mocked** half of M7: there is no real accounting system on the
other end today, and no real delivery mechanism (no webhook, no SFTP drop, no
API call) -- the artifact is written to object storage and signed, and that's
where the pipeline currently stops. The point is that the artifact itself is
production-shaped: a real integration could start consuming it unchanged.

## When an export happens

An invoice is exported once, automatically, the moment it reaches
`auto_approved` (system decision) or `approved` (human decision) --
see `services/approval.py` and `workers/tasks.py`. The resulting artifact is
a JSON file written to object storage at:

```
exports/<invoice_id>/<export_id>.json
```

and recorded as one row in the `exports` table (`storage_key`,
`contract_version`, `signature`, `created_at`). Re-exporting an invoice (not
currently triggered by anything, but supported) creates a new row and a new
artifact rather than overwriting the old one -- the export history, like
every other stage's history in this system, is append-only.

## Schema (`contract_version: "1"`)

```json
{
  "contract_version": "1",
  "invoice_id": "3fa9...uuid",
  "status": "auto_approved",
  "supplier_name": "ACME Supplies Ltd",
  "supplier_tax_id": "IT01234567890",
  "invoice_number": "INV-2026-0042",
  "invoice_date": "2026-02-03",
  "currency": "EUR",
  "subtotal": "100.00",
  "tax_amount": "22.00",
  "total": "122.00",
  "line_items": [
    {"description": "Widget", "quantity": "10", "unit_price": "5.00", "line_total": "50.00"}
  ],
  "vendor_id": "8c21...uuid",
  "vendor_name": "ACME Supplies Ltd",
  "po_id": "91ab...uuid",
  "po_number": "PO-5567",
  "approved_by": "system",
  "approved_reason": "all checks passed",
  "exported_at": "2026-02-03T10:15:00+00:00"
}
```

Field notes:

- `status` is the invoice's status *at the moment of export* (`auto_approved`
  or `approved`) -- not its current status, which by the time you read the
  artifact has already moved on to `exporting`/`exported`.
- Every invoice-detail field (`supplier_name` through `total`) is exactly
  what the extraction stage produced -- see the root README's "AI handles
  ambiguity, deterministic code enforces rules" section for what already
  stood between that raw extraction and this artifact existing at all
  (matching, 7 validation rules, the approval policy).
- `vendor_id`/`po_id` are `null` when matching didn't resolve one (this only
  reaches export at all if the approval policy required both, so in
  practice both are always present for an export produced by this
  pipeline -- they stay nullable in the schema for forward-compatibility).
- `approved_by` is `"system"` for an automatic approval, or the reviewer's
  name/email for a human one.

## Verifying the signature

Every artifact is signed with HMAC-SHA256 over the **exact bytes written to
storage** (`json.dumps(payload, sort_keys=True, separators=(",", ":"))`,
UTF-8 encoded -- canonical, so the same logical payload always signs the same
way). The secret is `EXPORT_SIGNING_SECRET` (see `.env.example`; the
committed default is intentionally insecure and must be overridden before
this ever points at anything real).

```python
import hashlib, hmac


def verify(payload_bytes: bytes, secret: str, signature: str) -> bool:
    expected = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

`invoice_ops.domain.export.verify_signature` is the reference implementation.

## Versioning

`contract_version` is a plain string, currently `"1"`. A breaking schema
change increments it; a consumer should reject (or branch on) any version it
doesn't recognize rather than guessing at field meaning.
