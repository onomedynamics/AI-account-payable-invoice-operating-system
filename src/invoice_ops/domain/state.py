"""Invoice lifecycle: the states and the legal moves between them.

Pure data and pure functions, no I/O. Callers ask ``can_transition`` before
writing a new status; the write itself (and the audit-log entry) happens in the
service or task layer. Full enforcement wiring lands in M5.
"""

from __future__ import annotations

from enum import StrEnum


class InvoiceStatus(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    MATCHING = "matching"
    VALIDATING = "validating"
    NEEDS_REVIEW = "needs_review"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPORTING = "exporting"
    EXPORTED = "exported"
    FAILED = "failed"


# Each status maps to the statuses it may move to next. A status absent from
# this map is terminal. FAILED is terminal for now; making failed invoices
# retryable is a deliberate later decision that needs a retry policy.
_TRANSITIONS: dict[InvoiceStatus, frozenset[InvoiceStatus]] = {
    InvoiceStatus.RECEIVED: frozenset({InvoiceStatus.EXTRACTING, InvoiceStatus.FAILED}),
    InvoiceStatus.EXTRACTING: frozenset({InvoiceStatus.EXTRACTED, InvoiceStatus.FAILED}),
    InvoiceStatus.EXTRACTED: frozenset({InvoiceStatus.MATCHING, InvoiceStatus.FAILED}),
    InvoiceStatus.MATCHING: frozenset({InvoiceStatus.VALIDATING, InvoiceStatus.FAILED}),
    InvoiceStatus.VALIDATING: frozenset(
        {
            InvoiceStatus.AUTO_APPROVED,
            InvoiceStatus.NEEDS_REVIEW,
            InvoiceStatus.FAILED,
        }
    ),
    InvoiceStatus.NEEDS_REVIEW: frozenset({InvoiceStatus.APPROVED, InvoiceStatus.REJECTED}),
    InvoiceStatus.AUTO_APPROVED: frozenset({InvoiceStatus.EXPORTING}),
    InvoiceStatus.APPROVED: frozenset({InvoiceStatus.EXPORTING}),
    InvoiceStatus.EXPORTING: frozenset({InvoiceStatus.EXPORTED, InvoiceStatus.FAILED}),
}

TERMINAL_STATUSES: frozenset[InvoiceStatus] = frozenset(
    s for s in InvoiceStatus if s not in _TRANSITIONS
)


def allowed_transitions(current: InvoiceStatus) -> frozenset[InvoiceStatus]:
    return _TRANSITIONS.get(current, frozenset())


def can_transition(current: InvoiceStatus, target: InvoiceStatus) -> bool:
    return target in allowed_transitions(current)


def is_terminal(status: InvoiceStatus) -> bool:
    return status in TERMINAL_STATUSES
