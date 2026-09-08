from __future__ import annotations

import itertools

import pytest

from invoice_ops.domain.state import (
    InvoiceStatus,
    allowed_transitions,
    can_transition,
    is_terminal,
)


def test_happy_path_chain_is_legal():
    chain = [
        InvoiceStatus.RECEIVED,
        InvoiceStatus.EXTRACTING,
        InvoiceStatus.EXTRACTED,
        InvoiceStatus.MATCHING,
        InvoiceStatus.VALIDATING,
        InvoiceStatus.NEEDS_REVIEW,
        InvoiceStatus.APPROVED,
        InvoiceStatus.EXPORTING,
        InvoiceStatus.EXPORTED,
    ]
    for current, target in itertools.pairwise(chain):
        assert can_transition(current, target), f"{current} -> {target} should be legal"


def test_auto_approved_branch_is_legal():
    assert can_transition(InvoiceStatus.VALIDATING, InvoiceStatus.AUTO_APPROVED)
    assert can_transition(InvoiceStatus.AUTO_APPROVED, InvoiceStatus.EXPORTING)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (InvoiceStatus.RECEIVED, InvoiceStatus.APPROVED),
        (InvoiceStatus.RECEIVED, InvoiceStatus.EXPORTED),
        (InvoiceStatus.EXPORTED, InvoiceStatus.RECEIVED),
        (InvoiceStatus.REJECTED, InvoiceStatus.APPROVED),
        # must pass through needs_review or auto_approved, not straight to approved
        (InvoiceStatus.VALIDATING, InvoiceStatus.APPROVED),
    ],
)
def test_illegal_transitions_are_rejected(current, target):
    assert not can_transition(current, target)


def test_terminal_states_have_no_exits():
    for status in (InvoiceStatus.EXPORTED, InvoiceStatus.REJECTED, InvoiceStatus.FAILED):
        assert is_terminal(status)
        assert allowed_transitions(status) == frozenset()


def test_non_terminal_states_have_exits():
    for status in (InvoiceStatus.RECEIVED, InvoiceStatus.VALIDATING, InvoiceStatus.APPROVED):
        assert not is_terminal(status)
        assert allowed_transitions(status)
