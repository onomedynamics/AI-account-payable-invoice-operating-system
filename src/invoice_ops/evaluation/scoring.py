"""Score one extracted invoice against ground truth, field by field."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from invoice_ops.domain.extraction import CRITICAL_FIELDS, ExtractedInvoice

SCALAR_FIELDS: tuple[str, ...] = (
    "supplier_name",
    "supplier_tax_id",
    "invoice_number",
    "invoice_date",
    "currency",
    "purchase_order_number",
    "subtotal",
    "tax_amount",
    "total",
)
_MONEY_FIELDS: frozenset[str] = frozenset({"subtotal", "tax_amount", "total"})


class Verdict(StrEnum):
    CORRECT = "correct"  # both present and equal
    WRONG = "wrong"  # both present, different
    MISSING = "missing"  # truth has a value, extraction is null
    SPURIOUS = "spurious"  # truth is null, extraction invented a value
    ABSENT_OK = "absent_ok"  # both null -- not a graded prediction


@dataclass(frozen=True)
class FieldScore:
    field: str
    verdict: Verdict
    expected: Any
    actual: Any


def _norm_text(value: Any) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).strip().lower().split())


def _norm_money(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return str(value).strip().lower()


def _norm_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _verdict(field: str, expected: Any, actual: Any) -> Verdict:
    if field in _MONEY_FIELDS:
        exp, act = _norm_money(expected), _norm_money(actual)
    elif field == "invoice_date":
        exp, act = _norm_date(expected), _norm_date(actual)
    else:
        exp, act = _norm_text(expected), _norm_text(actual)

    if exp is None and act is None:
        return Verdict.ABSENT_OK
    if exp is not None and act is None:
        return Verdict.MISSING
    if exp is None and act is not None:
        return Verdict.SPURIOUS
    return Verdict.CORRECT if exp == act else Verdict.WRONG


def score_invoice(expected: ExtractedInvoice, actual: ExtractedInvoice) -> list[FieldScore]:
    scores = [
        FieldScore(
            field=name,
            verdict=_verdict(name, getattr(expected, name), getattr(actual, name)),
            expected=getattr(expected, name),
            actual=getattr(actual, name),
        )
        for name in SCALAR_FIELDS
    ]
    exp_n, act_n = len(expected.line_items), len(actual.line_items)
    scores.append(
        FieldScore(
            field="line_items_count",
            verdict=Verdict.CORRECT if exp_n == act_n else Verdict.WRONG,
            expected=exp_n,
            actual=act_n,
        )
    )
    return scores


@dataclass(frozen=True)
class Aggregate:
    graded: int
    correct: int
    wrong: int
    missing: int
    spurious: int
    absent_ok: int
    critical_graded: int
    critical_correct: int

    @property
    def field_accuracy(self) -> float:
        return self.correct / self.graded if self.graded else 1.0

    @property
    def critical_accuracy(self) -> float:
        return self.critical_correct / self.critical_graded if self.critical_graded else 1.0


def aggregate(per_invoice: Sequence[Sequence[FieldScore]]) -> Aggregate:
    flat = [s for invoice in per_invoice for s in invoice]

    def n(verdict: Verdict, pool: list[FieldScore]) -> int:
        return sum(1 for s in pool if s.verdict == verdict)

    graded_pool = [s for s in flat if s.verdict is not Verdict.ABSENT_OK]
    crit_pool = [s for s in graded_pool if s.field in CRITICAL_FIELDS]

    return Aggregate(
        graded=len(graded_pool),
        correct=n(Verdict.CORRECT, graded_pool),
        wrong=n(Verdict.WRONG, graded_pool),
        missing=n(Verdict.MISSING, graded_pool),
        spurious=n(Verdict.SPURIOUS, graded_pool),
        absent_ok=sum(1 for s in flat if s.verdict is Verdict.ABSENT_OK),
        critical_graded=len(crit_pool),
        critical_correct=n(Verdict.CORRECT, crit_pool),
    )
