# Extraction eval harness

Measures how well the LLM extraction stage reads real invoices.

## Adding a fixture

For each invoice, drop two files in `evals/fixtures/`:

| File | What |
| --- | --- |
| `<name>.pdf` | the invoice, **redacted** (this repo is public) |
| `<name>.expected.json` | the ground-truth `ExtractedInvoice` -- copy `_TEMPLATE.expected.json` and fill it in |

Rules for the expected JSON:

- Use `null` for a field genuinely not on the invoice (do not invent one).
- Money as plain strings: `"61.00"`, no symbols or thousands separators.
- Dates as `YYYY-MM-DD`.
- `line_items` order does not matter for scoring; only the count is graded for now.

A scanned PDF with no text layer is fine to include -- the harness reports it as
"needs OCR" and skips it, which is the honest current behaviour.

## Running

```bash
OPENROUTER_API_KEY=sk-or-... uv run python evals/run.py
# or: just eval
```

It calls a real model (`LLM_MODEL`, default `openai/gpt-4o-mini`), so it costs a
few cents per run and is never part of CI.

## Metrics

Per field, one of: `correct`, `wrong`, `missing` (truth had a value, model
returned null), `spurious` (truth was null, model invented a value), `absent_ok`
(both null -- not graded).

- **field accuracy** = correct / (correct + wrong + missing + spurious)
- **critical accuracy** = same, restricted to `supplier_name`, `invoice_number`,
  `invoice_date`, `currency`, `total`

The scoring logic lives in `src/invoice_ops/evaluation/scoring.py` and is unit
tested; only the model call here needs the network.
