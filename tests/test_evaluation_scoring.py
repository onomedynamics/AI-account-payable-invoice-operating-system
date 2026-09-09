from __future__ import annotations

from invoice_ops.domain.extraction import ExtractedInvoice, LineItem
from invoice_ops.evaluation.scoring import Verdict, aggregate, score_invoice


def _inv(**kw) -> ExtractedInvoice:
    return ExtractedInvoice(**kw)


def test_exact_match_is_all_correct():
    truth = _inv(
        supplier_name="ACME Ltd",
        invoice_number="INV-1",
        invoice_date="2026-01-02",
        currency="EUR",
        total="61.00",
    )
    got = _inv(
        supplier_name="ACME Ltd",
        invoice_number="INV-1",
        invoice_date="2026-01-02",
        currency="EUR",
        total="61.00",
    )
    scores = {s.field: s.verdict for s in score_invoice(truth, got)}
    assert scores["supplier_name"] is Verdict.CORRECT
    assert scores["total"] is Verdict.CORRECT
    assert scores["supplier_tax_id"] is Verdict.ABSENT_OK


def test_money_is_normalized():
    truth = _inv(total="1234.5")
    got = _inv(total="1234.50")
    by_field = {s.field: s.verdict for s in score_invoice(truth, got)}
    assert by_field["total"] is Verdict.CORRECT


def test_text_is_case_and_space_insensitive():
    truth = _inv(supplier_name="ACME  Ltd")
    got = _inv(supplier_name="acme ltd")
    assert score_invoice(truth, got)[0].verdict is Verdict.CORRECT


def test_missing_vs_spurious_vs_wrong():
    truth = _inv(supplier_name="ACME", invoice_number="INV-1")
    got = _inv(supplier_name="OTHER", purchase_order_number="PO-9")
    by_field = {s.field: s.verdict for s in score_invoice(truth, got)}
    assert by_field["supplier_name"] is Verdict.WRONG
    assert by_field["invoice_number"] is Verdict.MISSING
    assert by_field["purchase_order_number"] is Verdict.SPURIOUS


def test_line_item_count_is_scored():
    truth = _inv(line_items=[LineItem(description="a"), LineItem(description="b")])
    got = _inv(line_items=[LineItem(description="a")])
    by_field = {s.field: s.verdict for s in score_invoice(truth, got)}
    assert by_field["line_items_count"] is Verdict.WRONG


def test_aggregate_accuracy_excludes_absent_ok():
    truth = _inv(supplier_name="ACME", invoice_number="INV-1", total="10.00")
    got = _inv(supplier_name="ACME", invoice_number="WRONG", total="10.00")
    agg = aggregate([score_invoice(truth, got)])
    # 3 graded scalars + line_items_count(both 0 -> CORRECT) = 4 graded
    assert agg.correct == 3
    assert agg.wrong == 1
    assert agg.absent_ok > 0
    assert abs(agg.field_accuracy - 0.75) < 1e-9
    assert agg.critical_graded == 3  # supplier_name, invoice_number, total
    assert agg.critical_correct == 2
