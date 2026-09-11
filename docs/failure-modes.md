# Failure modes and known limitations

What the two eval runs (`evals/run.py` for extraction, `evals/discrepancy_eval.py`
for matching/validation/approval) actually found, run against 8 real supplier
invoices to Leader Frutta dei F.lli Lanzafame Srl (7 distinct real suppliers,
2 document formats: a native PDF and photographed printed invoices).

## Results

| Eval | Metric | Result |
|---|---|---|
| Extraction (`evals/run.py`) | Field accuracy | 100% (72/72) |
| Extraction | Critical-field accuracy | 100% (40/40) |
| Discrepancy detection (`evals/discrepancy_eval.py`) | Precision | 100% (48/48 flagged cases were real) |
| Discrepancy detection | Recall | 100% (48/48 real discrepancies caught) |

Both are small-sample results (8 invoices, 7 scenario variants each) from one
business's supplier mix. Treat the 100%s as "no bugs found in this sample,"
not "guaranteed accurate in general" -- see Limitations below.

## Three real findings, fixed

**1. Currency inference.** The first eval run scored 88.9% because the model
correctly refused to guess a currency that was never printed on the invoice
("never guess" was the system prompt's own instruction). Real Italian
domestic invoices essentially never print "EUR" explicitly. Added a narrow
carve-out: infer currency from strong contextual signals (country, address,
decimal-comma formatting), scoped to currency only -- every other field kept
the strict no-guessing rule. All 8 invoices (1 tuned on, 7 held out)
extracted the correct currency afterward, so the fix generalized rather than
overfitting to the first sample.

**2. Ambiguous invoice-number label.** One real document's document-number
field read `3653 03 / 2026` -- an unusual compound format. The model
returned the clean `3653`; the ground-truth label originally demanded the
full compound string. Fixed the label, not the model: `3653` is what a
bookkeeper would actually file this under, and it's what this system's own
duplicate-detection and PO-matching need (a stable short identifier, not a
field-scramble). A genuine reminder that "ground truth" for a real-world
document is sometimes a judgment call, not a fact to transcribe.

**3. `line_items_sum_to_subtotal` blocked every real invoice.** The first
discrepancy-eval run found 0/8 "clean" invoices auto-approved -- every one
was blocked by this rule at `severity="error"`. Real invoices from this
supplier mix routinely carry small ancillary charges (stamp duty, handling
fees, packaging deposits) that are not priced line items in the extraction
schema, so line-items-sum essentially never matches subtotal exactly on a
genuine invoice: gaps of €0.25-€2.10 were normal, not exceptional. A rule
that fires on 100% of real invoices provides no signal -- fixed by
downgrading it from `error` to `warning` (visible, non-blocking). The
correct long-term fix is a structured "other charges" field in the
extraction schema so this can be checked exactly instead of approximately;
that's a real schema change, out of scope for a one-line severity fix.

## One finding surfaced, not fixed -- needs a decision

**None of the 8 real invoices reference a purchase order.** This business
runs same-day spot-market produce purchases (buy today's tomatoes at
today's market price), not PO-backed ordering. The approval policy
(`domain/approval.py`) requires a matched PO for auto-approval on the theory
that "no PO" means no purchase-order control was exercised. Taken at face
value, that means **100% of this business's real day-to-day invoices would
always route to human review**, regardless of how clean everything else is
-- auto-approval would never fire in practice for their actual workflow.

This is not a bug to silently patch; it's a real policy question:

- Keep the PO requirement as-is: safest, but auto-approval is effectively
  unused for spot-market purchases. Every invoice needs a human regardless
  of quality.
- Relax it for known/trusted recurring vendors (the ones already in the
  vendor table, matched with high confidence) below some amount threshold.
- Track a running per-vendor spend pattern and flag only invoices that
  deviate from it, instead of requiring a PO at all for this class of
  purchase.

`evals/discrepancy_eval.py`'s scenarios seed a synthetic PO for every
invoice specifically to exercise the PO-comparison rules despite this gap --
that's a deliberate choice for testing the machinery, not a claim about how
the business operates today.

## Known limitations (not yet tested, not yet broken)

- **Sample size.** 8 real invoices, all in Italian, all produce/wholesale,
  all from businesses in the same region. No evidence yet for other
  languages, industries, or invoice-generation software.
- **No OCR fallback.** A scanned/photographed invoice with no embedded text
  layer still hard-fails extraction (`services/documents.py`). Several of
  the source documents for these fixtures *were* photographs -- but they had
  real, extractable text layers (phone-camera PDF scan apps often add one).
  A pure image with no text layer is untested.
- **Vendor fuzzy-matching thresholds** (`FUZZY_AUTO_ACCEPT = 0.88`,
  `FUZZY_AMBIGUITY_GAP = 0.08` in `domain/matching.py`) were chosen by
  reasoning, not calibrated against a labelled name-variation dataset.
- **PO tolerance is a flat 2%** with no absolute floor -- a documented
  simplification (see the comment in `domain/validation.py`); a large
  invoice a fraction over 2% and a tiny invoice a fraction over 2% are
  treated identically, which a real policy likely would not do.
- **Single currency per invoice, no FX.** An invoice can't reference a PO
  issued in a different currency in any meaningful way today.
- **No 3-way match** (invoice vs PO vs goods receipt) -- explicitly out of
  scope until M9.
