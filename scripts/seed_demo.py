"""Insert a couple of demo vendors + purchase orders so matching has something
to match against when you test the running server by hand.

    uv run python scripts/seed_demo.py
    (or: just seed)

Safe to run more than once: skips a vendor if its tax_id already exists.
"""

from __future__ import annotations

from decimal import Decimal

from invoice_ops.db import session_scope
from invoice_ops.models import PoLine, PurchaseOrder, Vendor


def main() -> None:
    with session_scope() as session:
        existing = {v.tax_id for v in session.query(Vendor).all() if v.tax_id is not None}

        if "IT01234567890" not in existing:
            acme = Vendor(
                legal_name="ACME Supplies Ltd",
                tax_id="IT01234567890",
                aliases=["ACME", "ACME Supplies"],
                country="IT",
            )
            session.add(acme)
            session.flush()

            po = PurchaseOrder(
                po_number="PO-5567",
                vendor_id=acme.id,
                currency="EUR",
                subtotal=Decimal("100.00"),
                tax_amount=Decimal("22.00"),
                total=Decimal("122.00"),
                status="open",
            )
            session.add(po)
            session.flush()

            session.add_all(
                [
                    PoLine(
                        po_id=po.id,
                        description="Widget blue 10mm",
                        quantity=Decimal("10"),
                        unit_price=Decimal("5.00"),
                        line_total=Decimal("50.00"),
                    ),
                    PoLine(
                        po_id=po.id,
                        description="Bracket steel",
                        quantity=Decimal("4"),
                        unit_price=Decimal("12.50"),
                        line_total=Decimal("50.00"),
                    ),
                ]
            )
            print(f"seeded vendor {acme.legal_name!r} with PO {po.po_number!r}")
        else:
            print("ACME Supplies Ltd already seeded, skipping")

        if "IT99999999999" not in existing:
            session.add(
                Vendor(
                    legal_name="Northwind Traders SRL",
                    tax_id="IT99999999999",
                    aliases=["Northwind"],
                    country="IT",
                )
            )
            print("seeded vendor 'Northwind Traders SRL' (no PO)")
        else:
            print("Northwind Traders SRL already seeded, skipping")


if __name__ == "__main__":
    main()
