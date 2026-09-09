# invoice-ops

An AI Accounts Payable / Invoice Operations system: invoices arrive by upload
(email and API later), get read and structured, matched against vendors and
purchase orders, validated against deterministic financial rules, and either
auto-approved or routed to a human before export to accounting.

```
invoice
  -> document understanding (OCR + layout)
  -> structured extraction (LLM, schema-constrained, per-field confidence)
  -> vendor identification
  -> PO matching
  -> deterministic validation
  -> anomaly / duplicate detection
  -> approval decision
  -> human review (only when needed)
  -> accounting export
```

## Design principle: AI handles ambiguity, deterministic code enforces financial rules

This is the point of the project, not an implementation detail.

**The LLM is used only where the task is genuinely interpretive:** turning a
messy PDF or scan into structured fields, and reconciling ambiguous vendor
names. Its output is always schema-constrained and always carries a per-field
confidence.

**Every decision with financial consequences is made by deterministic code:**
Are the line items arithmetic consistent? Does the invoice total exceed the PO
beyond tolerance? Is the tax recomputation correct? Is this a duplicate of an
invoice already paid? Should this auto-approve or wait for a human? These are
pure functions with unit tests, not model calls.

**Confidence flows into the gate.** An invoice auto-approves only when it is
inside every tolerance *and* extraction confidence clears a threshold on the
fields that matter. Anything else goes to the review queue with the uncertain
fields flagged. The system never pays on a guess.

Example of the intended behaviour:

> Invoice total ₦4,850,000. Matched PO ₦4,200,000.
> Invoice exceeds PO by ₦650,000 (15.5%). Auto-approval blocked; routed to
> procurement review.

That sentence is produced by a validation rule, not by asking a model what it
thinks.

## Architecture

| Layer | Choice |
| --- | --- |
| API | FastAPI |
| Workers | Celery (Redis broker in prod; in-process eager mode in dev) |
| Database | PostgreSQL (SQLite in dev) via SQLAlchemy 2.0 + Alembic |
| Object storage | S3 / MinIO in prod; local filesystem in dev (same `Storage` interface) |
| OCR / layout | open-source (docTR / PaddleOCR / pdfplumber) -- decided in M2 |
| Extraction | LLM with schema-constrained output + validate-and-retry |

Dev has no external service dependencies. `docker-compose.yml` and CI run the
real Postgres + Redis so the prod-shaped path is always exercised.

## Quickstart

```bash
uv sync
cp .env.example .env          # dev defaults need no services
uv run alembic upgrade head
uv run uvicorn invoice_ops.api.main:app --reload
# GET http://127.0.0.1:8000/health/ready
```

`just` recipes wrap the common commands (`just api`, `just test`, `just lint`,
`just migrate`).

## Status

Milestone-driven. Each milestone is a demoable vertical slice.

- [x] **M0** Skeleton: config, DB/storage/queue interfaces, health probes, CI
- [x] **M1** Idempotent upload + raw-document storage + `invoices` row
- [~] **M2** LLM extraction into a strict schema with confidence
  - [x] **M2a** pdfplumber text + `ExtractedInvoice` schema + `Extraction` model + OpenRouter client + validate-and-retry, wired into the task
  - [ ] **M2b** eval harness against labelled real invoices + one live smoke test
  - [ ] later: OCR fallback for scanned PDFs / image uploads
- [ ] **M3** Deterministic vendor + PO matching
- [ ] **M4** Validation engine (per-rule unit tests)
- [ ] **M5** Approval policy + invoice lifecycle state machine + audit log
- [ ] **M6** Human review UI
- [ ] **M7** Mocked accounting export (signed artifact + contract doc)
- [ ] **M8** Full eval harness: discrepancy precision/recall + failure-modes writeup
- [ ] **M9** (optional) Email ingestion, 3-way match with goods receipt

## Out of scope

Real ERP connectors (QuickBooks/Xero/NetSuite/SAP), multi-tenant auth/SSO,
payment execution, model retraining/feedback loops, mobile.
