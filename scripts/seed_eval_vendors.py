"""Seed vendors matching the real (safely-reconstructed) invoices in
evals/fixtures/, so uploading one of those PDFs through the review UI
actually resolves a vendor via tax_id -- the same as it would for the real
business's real invoices.

None of these get a purchase order, on purpose: the real invoices don't
reference one either (see docs/failure-modes.md). Uploading one of these
PDFs after seeding will correctly land in needs_review -- that's the
confirmed policy working as designed, live in the running app, not a bug.

For the auto-approve path instead, use `just seed` (a separate demo vendor
that does have a matching PO).

    uv run python scripts/seed_eval_vendors.py
    (or: just seed-eval-vendors)

Safe to run more than once: skips any tax_id already present.
"""

from __future__ import annotations

import json
from pathlib import Path

from invoice_ops.db import session_scope
from invoice_ops.models import Vendor

FIXTURES = Path(__file__).resolve().parent.parent / "evals" / "fixtures"


def main() -> None:
    with session_scope() as session:
        existing = {v.tax_id for v in session.query(Vendor).all() if v.tax_id}
        seeded = 0

        for path in sorted(FIXTURES.glob("*.expected.json")):
            if path.stem.startswith("_TEMPLATE"):
                continue
            data = json.loads(path.read_text())
            tax_id = data.get("supplier_tax_id")
            name = data.get("supplier_name")
            if not tax_id or not name or tax_id in existing:
                continue
            session.add(Vendor(legal_name=name, tax_id=tax_id, aliases=[], country="IT"))
            existing.add(tax_id)
            seeded += 1
            print(f"seeded vendor: {name} ({tax_id})")

        if seeded == 0:
            print("nothing to seed (vendors already present, or no fixtures found)")


if __name__ == "__main__":
    main()
