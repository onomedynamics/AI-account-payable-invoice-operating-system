"""Live smoke test: one real OpenRouter call. Not run in CI.

OPENROUTER_API_KEY=... uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.getenv("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
def test_real_openrouter_extraction(make_pdf):
    from invoice_ops.config import get_settings
    from invoice_ops.services.documents import extract_text
    from invoice_ops.services.extraction import extract_with_retry
    from invoice_ops.services.llm import OpenRouterClient

    get_settings.cache_clear()
    settings = get_settings()

    pdf = make_pdf(
        "ACME SUPPLIES LTD\n"
        "VAT IT01234567890\n"
        "Invoice INV-2026-0042\n"
        "Date 2026-02-03\n"
        "10 x Widget @ 5.00 = 50.00\n"
        "Subtotal 50.00\n"
        "VAT 22% 11.00\n"
        "Total 61.00 EUR\n"
    )
    document = extract_text(pdf, "application/pdf")
    outcome = extract_with_retry(
        OpenRouterClient(settings), document.text, max_retries=settings.llm_max_retries
    )

    invoice = outcome.result.invoice
    assert invoice.invoice_number
    assert invoice.total is not None
