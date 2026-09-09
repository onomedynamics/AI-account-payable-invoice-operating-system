"""Extraction eval harness.

Runs every ``evals/fixtures/*.pdf`` through pdfplumber + a real OpenRouter
model, scores the result against ``<name>.expected.json``, and prints
per-invoice notes plus an aggregate.

    OPENROUTER_API_KEY=... uv run python evals/run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from invoice_ops.config import get_settings
from invoice_ops.domain.extraction import ExtractedInvoice
from invoice_ops.evaluation.scoring import FieldScore, Verdict, aggregate, score_invoice
from invoice_ops.services.documents import extract_text
from invoice_ops.services.extraction import ExtractionFailed, extract_with_retry
from invoice_ops.services.llm import LLMError, OpenRouterClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_expected(path: Path) -> ExtractedInvoice:
    return ExtractedInvoice.model_validate_json(path.read_text(encoding="utf-8"))


def main() -> int:
    settings = get_settings()
    pdfs = sorted(FIXTURES.glob("*.pdf"))
    if not pdfs:
        print(f"no fixtures in {FIXTURES}/*.pdf")
        return 1

    try:
        client = OpenRouterClient(settings)
    except LLMError as exc:
        print(f"cannot start: {exc}")
        return 2

    per_invoice: list[list[FieldScore]] = []
    for pdf in pdfs:
        expected_path = pdf.with_suffix(".expected.json")
        if not expected_path.exists():
            print(f"skip {pdf.name}: no {expected_path.name}")
            continue

        document = extract_text(pdf.read_bytes(), "application/pdf")
        if document.is_empty:
            print(f"{pdf.name:34} no text layer (needs OCR) -- skipped")
            continue

        try:
            outcome = extract_with_retry(
                client, document.text, max_retries=settings.llm_max_retries
            )
        except ExtractionFailed as exc:
            print(f"{pdf.name:34} FAILED after {exc.attempts} attempts: {exc}")
            continue

        scores = score_invoice(_load_expected(expected_path), outcome.result.invoice)
        per_invoice.append(scores)
        problems = [s.field for s in scores if s.verdict in (Verdict.WRONG, Verdict.MISSING)]
        note = "ok" if not problems else f"issues: {', '.join(problems)}"
        print(f"{pdf.name:34} attempts={outcome.attempts}  {note}")

    if not per_invoice:
        print("nothing scored")
        return 1

    agg = aggregate(per_invoice)
    print("\n--- aggregate ---")
    print(f"invoices scored    {len(per_invoice)}")
    print(f"field accuracy     {agg.field_accuracy:.1%}  ({agg.correct}/{agg.graded})")
    print(
        f"critical accuracy  {agg.critical_accuracy:.1%}  "
        f"({agg.critical_correct}/{agg.critical_graded})"
    )
    print(
        f"wrong={agg.wrong}  missing={agg.missing}  "
        f"spurious={agg.spurious}  absent_ok={agg.absent_ok}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
