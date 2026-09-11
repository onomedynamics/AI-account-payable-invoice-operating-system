"""M8: precision/recall of the deterministic discrepancy-detection layer.

Unlike evals/run.py (which measures extraction accuracy against a real LLM),
this measures the layer *after* extraction: vendor/PO matching, the 7
validation rules, and the approval policy -- all pure functions, no network,
no database. It reuses the real invoices in evals/fixtures/ as the source of
realistic data (trusting their extraction, which evals/run.py separately
measured at 100% field accuracy), then builds seven labelled variants of each:
one clean case and six deliberately-broken ones, each with a known-correct
expected outcome by construction.

    uv run python evals/discrepancy_eval.py
"""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

from invoice_ops.domain.approval import decide_approval
from invoice_ops.domain.extraction import CRITICAL_FIELDS, ExtractedInvoice
from invoice_ops.domain.matching import PoIdentity, VendorIdentity, match_po, match_vendor
from invoice_ops.domain.validation import (
    DuplicateCandidate,
    PoSnapshot,
    ValidationInput,
    has_blocking_failure,
    run_rules,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FULL_CONFIDENCE = dict.fromkeys(CRITICAL_FIELDS, 0.95)


def _load(name: str) -> ExtractedInvoice:
    data = json.loads((FIXTURES / f"{name}.expected.json").read_text())
    return ExtractedInvoice.model_validate(data)


def _decide(
    invoice: ExtractedInvoice,
    vendor: VendorIdentity,
    po: PoIdentity | None,
    po_total: Decimal,
    po_currency: str,
    *,
    other_invoices_same_number: tuple[ExtractedInvoice, ...] = (),
) -> bool:
    """Run the real pipeline. Returns True if the invoice needed review."""
    vendor_result = match_vendor(invoice, [vendor])
    po_result = match_po(invoice, [po] if po else [])

    matched_po = None
    if po_result.po_id is not None:
        matched_po = PoSnapshot(
            po_number=po.po_number, currency=po_currency, total=po_total, vendor_id=vendor.vendor_id
        )

    duplicates = tuple(
        DuplicateCandidate(
            invoice_id=uuid.uuid4(), invoice_number=str(o.invoice_number), status="approved"
        )
        for o in other_invoices_same_number
        if o.invoice_number == invoice.invoice_number
    )

    results = run_rules(
        ValidationInput(
            invoice=invoice,
            matched_po=matched_po,
            matched_vendor_id=vendor_result.vendor_id,
            duplicate_candidates=duplicates,
        )
    )
    validation_passed = not has_blocking_failure(results)
    vendor_identified = vendor_result.method in ("tax_id", "exact_name", "fuzzy_name")
    has_po = matched_po is not None

    decision = decide_approval(
        validation_passed=validation_passed,
        vendor_identified=vendor_identified,
        has_po=has_po,
        field_confidence=FULL_CONFIDENCE,
    )
    return decision.outcome.value == "needs_review"


def _scenarios(name: str) -> list[tuple[str, bool, bool]]:
    """Returns (scenario, expected_needs_review, actual_needs_review) triples."""
    raw = _load(name)
    vendor = VendorIdentity(
        vendor_id=uuid.uuid4(),
        legal_name=raw.supplier_name or "UNKNOWN",
        tax_id=raw.supplier_tax_id,
    )
    po_total = raw.total or Decimal("0")
    po_currency = raw.currency or "EUR"

    # None of these 8 real invoices actually carry a PO number -- this
    # business runs same-day spot-market produce purchases, not PO-backed
    # ordering (see docs/failure-modes.md). To exercise the PO-comparison
    # rules at all, seed a PO and have "invoice" (used for every scenario
    # except the explicit no_po one) reference it, as if this business did
    # operate PO-first. This is a scenario-construction choice for the eval,
    # not a claim about how Leader Frutta actually operates today.
    po_number = raw.purchase_order_number or f"PO-{name}"
    po = PoIdentity(po_id=uuid.uuid4(), po_number=po_number)
    invoice = raw.model_copy(update={"purchase_order_number": po_number})

    results = []

    # 1. Clean: invoice matches its own vendor + PO exactly.
    results.append(("clean", False, _decide(invoice, vendor, po, po_total, po_currency)))

    # 2. PO overage: invoice total 20% higher than the PO.
    over = invoice.model_copy(
        update={"total": (po_total * Decimal("1.20")).quantize(Decimal("0.01"))}
    )
    results.append(("po_overage", True, _decide(over, vendor, po, po_total, po_currency)))

    # 3. PO underage: invoice total 20% lower -- still an anomaly worth a look.
    under = invoice.model_copy(
        update={"total": (po_total * Decimal("0.80")).quantize(Decimal("0.01"))}
    )
    results.append(("po_underage", True, _decide(under, vendor, po, po_total, po_currency)))

    # 4. Duplicate: the same invoice number seen before, for the same vendor.
    results.append(
        (
            "duplicate_invoice",
            True,
            _decide(
                invoice, vendor, po, po_total, po_currency, other_invoices_same_number=(invoice,)
            ),
        )
    )

    # 5. Currency mismatch: invoice says a different currency than the PO.
    other_currency = "USD" if po_currency != "USD" else "GBP"
    mismatched = invoice.model_copy(update={"currency": other_currency})
    results.append(
        ("currency_mismatch", True, _decide(mismatched, vendor, po, po_total, po_currency))
    )

    # 6. Unknown vendor: supplier name/tax_id do not match anything on file.
    unknown = invoice.model_copy(
        update={"supplier_name": "Totally Unrelated Co", "supplier_tax_id": "IT00000099999"}
    )
    results.append(("unknown_vendor", True, _decide(unknown, vendor, po, po_total, po_currency)))

    # 7. No PO on the invoice at all.
    no_po = invoice.model_copy(update={"purchase_order_number": None})
    results.append(("no_po", True, _decide(no_po, vendor, po, po_total, po_currency)))

    return results


def main() -> int:
    names = sorted(p.stem.removesuffix(".expected") for p in FIXTURES.glob("*.expected.json"))
    names = [n for n in names if not n.startswith("_TEMPLATE")]
    if not names:
        print("no fixtures found")
        return 1

    tp = fp = tn = fn = 0
    per_scenario: dict[str, list[bool]] = {}

    for name in names:
        for scenario, expected, actual in _scenarios(name):
            per_scenario.setdefault(scenario, []).append(expected == actual)
            if expected and actual:
                tp += 1
            elif not expected and actual:
                fp += 1
            elif not expected and not actual:
                tn += 1
            else:
                fn += 1
                print(f"MISS  {name:24} {scenario:20} expected_review={expected} got={actual}")

    total = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print(
        f"\n--- discrepancy detection over {len(names)} invoices x 7 scenarios ({total} cases) ---"
    )
    print(f"precision  {precision:.1%}   (of flagged cases, how many were real discrepancies)")
    print(f"recall     {recall:.1%}   (of real discrepancies, how many got flagged)")
    print(f"f1         {f1:.1%}")
    print(f"tp={tp} fp={fp} tn={tn} fn={fn}")
    print("\nper-scenario accuracy:")
    for scenario, outcomes in per_scenario.items():
        acc = sum(outcomes) / len(outcomes)
        print(f"  {scenario:20} {acc:.1%}  ({sum(outcomes)}/{len(outcomes)})")

    return 0 if fn == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
